#!/usr/bin/env python3
"""Collect supervised SMB1 transition/risk samples from real Mesen rollouts.

This is an offline teacher-data tool for issue #22. Mesen remains the only
transition oracle. Greedy mode follows one best trajectory; hazard mode expands
a small breadth-first tree; hazard-beam keeps a fixed-width frontier so the
teacher can reach deeper hazard boundaries without giving up local diversity.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import CandidateTerminal, observation_from_state, read_smb1_state
from fami_pixel.learning import build_rollout_record, write_jsonl_record
from fami_pixel.learning.hazard_sampling import select_depth_beam, select_hazard_branches

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v7 as v7
import mesen_smb_checkpoint_planner_v10 as v10
import mesen_smb_checkpoint_planner_v11 as v11


_TERMINAL_PREFIX = "RolloutDataset:"
_SUPERVISOR_GRACE_S = 0.75


@dataclass(frozen=True)
class _RootState:
    checkpoint: Path
    frame: int
    x: int
    engine: int


@dataclass(frozen=True)
class _BeamChild:
    root: _RootState
    outcome: object
    observation: object
    child_x: int
    state_signature: tuple[int, int, int, int, int, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Mesen teacher rollouts for SMB1 surrogate learning")
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home-rollout-teacher"))
    parser.add_argument("--checkpoint-dir", type=Path, default=Path("build/checkpoints/rollout-teacher"))
    parser.add_argument("--output", type=Path, default=Path("build/datasets/smb1-rollouts.jsonl"))
    parser.add_argument("--roots", type=int, default=40, help="maximum root states to collect")
    parser.add_argument("--step-timeout", type=float, default=5.0)
    parser.add_argument(
        "--candidate-set",
        choices=("live", "coarse", "precision"),
        default="live",
        help="candidate family evaluated from each root checkpoint",
    )
    parser.add_argument(
        "--sampling",
        choices=("greedy", "hazard", "hazard-beam"),
        default="greedy",
        help=(
            "greedy follows one best path; hazard expands a breadth-first tree; "
            "hazard-beam prunes each depth to a fixed forward-moving beam"
        ),
    )
    parser.add_argument(
        "--branch-width",
        type=int,
        default=3,
        help="maximum children/beam width for hazard sampling modes",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="append to an existing dataset instead of replacing it for this collection run",
    )
    parser.add_argument("--collector-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.roots <= 0:
        parser.error("--roots must be > 0")
    if args.branch_width <= 0:
        parser.error("--branch-width must be > 0")
    return args


def candidate_pool(name: str):
    if name == "live":
        return tuple(v11._candidate_pool())
    if name == "coarse":
        return tuple(v7.COARSE_CANDIDATES)
    return tuple(v7.PRECISION_CANDIDATES)


def outcome_score(outcome) -> tuple[int, int, int, int]:
    return (
        -1 if outcome.terminal == CandidateTerminal.DEATH else 1,
        1 if outcome.reached_flagpole else 0,
        int(outcome.progress),
        int(outcome.max_x),
    )


def commit_candidate(core, candidate, step_timeout: float) -> None:
    for command in candidate.commands:
        base.set_nes_controller_state(core, 0, int(command.nes_buttons))
        for _ in range(int(command.frame_count)):
            base.step(core, step_timeout)
    base.set_nes_controller_state(core, 0, 0x00)


def _state_signature(observation) -> tuple[int, int, int, int, int, int]:
    return (
        int(observation.mario_x_abs),
        int(observation.mario_y),
        int(observation.player_x_speed),
        int(observation.player_y_speed),
        int(observation.player_state),
        int(observation.game_engine_subroutine),
    )


def _source_name(args: argparse.Namespace) -> str:
    base_name = f"offline-{args.candidate_set}"
    if args.sampling == "greedy":
        return base_name
    if args.sampling == "hazard":
        return f"{base_name}-hazard"
    return f"{base_name}-hazard-beam"


def _evaluate_root(
    core,
    candidates,
    checkpoint: Path,
    root_frame: int,
    root_x: int,
    root_engine: int,
    *,
    source: str,
    generation: int,
    output: Path,
    step_timeout: float,
):
    evaluated = []
    for candidate in candidates:
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        candidate_start = observation_from_state(core.frame_count(), read_smb1_state(core))
        outcome = v10.run_candidate_pit_aware(core, candidate, candidate_start, step_timeout)
        end = observation_from_state(core.frame_count(), read_smb1_state(core))
        record = build_rollout_record(
            candidate_start,
            end,
            outcome,
            source=source,
            generation=generation,
        )
        write_jsonl_record(output, record)
        evaluated.append(outcome)
    return evaluated


def _collect_greedy(core, args, candidates, checkpoint_dir: Path, output: Path, source: str, current):
    records = 0
    for root_index in range(args.roots):
        checkpoint = checkpoint_dir / f"root-{root_index:04d}.mss"
        root_frame, root_x, root_engine = base.save_checkpoint(core, checkpoint)
        start = observation_from_state(core.frame_count(), read_smb1_state(core))
        evaluated = _evaluate_root(
            core,
            candidates,
            checkpoint,
            root_frame,
            root_x,
            root_engine,
            source=source,
            generation=root_index,
            output=output,
            step_timeout=args.step_timeout,
        )
        records += len(evaluated)

        best = max(evaluated, key=outcome_score)
        print(
            f"root {root_index:03d} frame={start.native_frame_id} X={start.mario_x_abs} "
            f"-> {best.candidate.name} progress={best.progress:+d} terminal={best.terminal.value}",
            flush=True,
        )

        if best.terminal != CandidateTerminal.NONE:
            print(f"teacher stopped on terminal={best.terminal.value}", flush=True)
            break

        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        commit_candidate(core, best.candidate, args.step_timeout)
        current = observation_from_state(core.frame_count(), read_smb1_state(core))

    return records, current


def _collect_hazard(core, args, candidates, checkpoint_dir: Path, output: Path, source: str, current):
    """Breadth-first expansion that deliberately seeks distinct shallow hazard roots."""
    initial = checkpoint_dir / "hazard-root-0000.mss"
    root_frame, root_x, root_engine = base.save_checkpoint(core, initial)
    frontier = deque([(initial, root_frame, root_x, root_engine)])
    seen_states = {_state_signature(current)}
    records = 0

    for root_index in range(args.roots):
        if not frontier:
            print("hazard frontier exhausted", flush=True)
            break

        checkpoint, root_frame, root_x, root_engine = frontier.popleft()
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        start = observation_from_state(core.frame_count(), read_smb1_state(core))
        evaluated = _evaluate_root(
            core,
            candidates,
            checkpoint,
            root_frame,
            root_x,
            root_engine,
            source=source,
            generation=root_index,
            output=output,
            step_timeout=args.step_timeout,
        )
        records += len(evaluated)

        death_count = sum(outcome.terminal == CandidateTerminal.DEATH for outcome in evaluated)
        stalled_count = sum(int(outcome.progress) <= 0 for outcome in evaluated)
        branches = select_hazard_branches(evaluated, args.branch_width)
        print(
            f"root {root_index:03d} frame={start.native_frame_id} X={start.mario_x_abs} "
            f"death={death_count} no_progress={stalled_count} "
            f"branches={[outcome.candidate.name for outcome in branches]}",
            flush=True,
        )

        for branch_index, outcome in enumerate(branches):
            base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
            commit_candidate(core, outcome.candidate, args.step_timeout)
            child = observation_from_state(core.frame_count(), read_smb1_state(core))
            signature = _state_signature(child)
            if signature in seen_states:
                continue
            seen_states.add(signature)

            child_path = checkpoint_dir / f"hazard-child-{root_index:04d}-{branch_index:02d}.mss"
            child_frame, child_x, child_engine = base.save_checkpoint(core, child_path)
            frontier.append((child_path, child_frame, child_x, child_engine))
            current = child

    return records, current


def _collect_hazard_beam(core, args, candidates, checkpoint_dir: Path, output: Path, source: str, current):
    """Depth-oriented fixed-width beam for reaching deeper hazard boundaries."""
    initial_path = checkpoint_dir / "hazard-beam-root-0000.mss"
    root_frame, root_x, root_engine = base.save_checkpoint(core, initial_path)
    beam = [_RootState(initial_path, root_frame, root_x, root_engine)]
    seen_states = {_state_signature(current)}
    records = 0
    root_index = 0
    depth = 0

    while beam and root_index < args.roots:
        expanded: list[_BeamChild] = []
        print(f"beam depth {depth:03d} roots={len(beam)}", flush=True)

        for beam_slot, root in enumerate(beam):
            if root_index >= args.roots:
                break

            base.restore_checkpoint(core, root.checkpoint, root.frame, root.x, root.engine)
            start = observation_from_state(core.frame_count(), read_smb1_state(core))
            evaluated = _evaluate_root(
                core,
                candidates,
                root.checkpoint,
                root.frame,
                root.x,
                root.engine,
                source=source,
                generation=root_index,
                output=output,
                step_timeout=args.step_timeout,
            )
            records += len(evaluated)

            death_count = sum(outcome.terminal == CandidateTerminal.DEATH for outcome in evaluated)
            stalled_count = sum(int(outcome.progress) <= 0 for outcome in evaluated)
            branches = select_hazard_branches(evaluated, args.branch_width)
            print(
                f"root {root_index:03d} depth={depth:03d} slot={beam_slot} "
                f"frame={start.native_frame_id} X={start.mario_x_abs} "
                f"death={death_count} no_progress={stalled_count} "
                f"expand={[outcome.candidate.name for outcome in branches]}",
                flush=True,
            )

            for branch_index, outcome in enumerate(branches):
                base.restore_checkpoint(core, root.checkpoint, root.frame, root.x, root.engine)
                commit_candidate(core, outcome.candidate, args.step_timeout)
                child = observation_from_state(core.frame_count(), read_smb1_state(core))
                signature = _state_signature(child)
                if signature in seen_states:
                    continue
                seen_states.add(signature)

                child_path = checkpoint_dir / (
                    f"hazard-beam-d{depth:03d}-r{root_index:04d}-b{branch_index:02d}.mss"
                )
                child_frame, child_x, child_engine = base.save_checkpoint(core, child_path)
                child_root = _RootState(child_path, child_frame, child_x, child_engine)
                expanded.append(
                    _BeamChild(
                        root=child_root,
                        outcome=outcome,
                        observation=child,
                        child_x=int(child.mario_x_abs),
                        state_signature=signature,
                    )
                )

            root_index += 1

        selected = select_depth_beam(expanded, args.branch_width)
        beam = [entry.root for entry in selected]
        if selected:
            current = max(selected, key=lambda entry: int(entry.child_x)).observation
            print(
                f"beam keep depth={depth + 1:03d} "
                f"X={[int(entry.child_x) for entry in selected]} "
                f"via={[entry.outcome.candidate.name for entry in selected]}",
                flush=True,
            )
        else:
            print("hazard-beam frontier exhausted", flush=True)

        depth += 1

    return records, current


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        proc.kill()


def collector_main(args: argparse.Namespace) -> int:
    candidates = candidate_pool(args.candidate_set)
    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not args.append:
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom failed")
    core.initialize_debugger()

    state = base.enter_world_1_1(core, args.step_timeout)
    current = observation_from_state(core.frame_count(), state)
    source = _source_name(args)

    print(f"Teacher roots : {args.roots}", flush=True)
    print(f"Candidates    : {len(candidates)} ({args.candidate_set})", flush=True)
    print(f"Sampling      : {args.sampling}", flush=True)
    if args.sampling in ("hazard", "hazard-beam"):
        print(f"Branch width  : {args.branch_width}", flush=True)
    print(f"Source        : {source}", flush=True)
    print(f"Dataset       : {output}", flush=True)
    print(f"Output mode   : {'append' if args.append else 'replace'}", flush=True)

    if args.sampling == "hazard":
        records, current = _collect_hazard(
            core, args, candidates, checkpoint_dir, output, source, current
        )
    elif args.sampling == "hazard-beam":
        records, current = _collect_hazard_beam(
            core, args, candidates, checkpoint_dir, output, source, current
        )
    else:
        records, current = _collect_greedy(
            core, args, candidates, checkpoint_dir, output, source, current
        )

    print(f"records       : {records}", flush=True)
    print(f"final frame   : {current.native_frame_id}", flush=True)
    print(f"final X       : {current.mario_x_abs}", flush=True)
    print(f"RolloutDataset: COMPLETE records={records} output={output}", flush=True)
    return 0


def supervise_main() -> int:
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--collector-worker",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    for line in iter(proc.stdout.readline, ""):
        print(line, end="", flush=True)
        if _TERMINAL_PREFIX not in line:
            continue
        try:
            return proc.wait(timeout=_SUPERVISOR_GRACE_S)
        except subprocess.TimeoutExpired:
            print("RolloutDataset supervisor: collection complete; terminating native process tree", flush=True)
            _terminate_process_tree(proc)
            return 0

    return proc.wait()


def main() -> int:
    args = parse_args()
    if args.collector_worker:
        return collector_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
