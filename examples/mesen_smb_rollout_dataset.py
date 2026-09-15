#!/usr/bin/env python3
"""Collect supervised SMB1 transition/risk samples from real Mesen rollouts.

Mesen remains the only transition/terminal oracle. ``hazard-beam-probe`` adds
an authoritative neutral continuation after selected surviving candidates so
delayed death / doomed-state labels can be measured instead of inferred.
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
from fami_pixel.games.smb1 import (
    ActionCommand,
    CandidateTerminal,
    PlanCandidate,
    Smb1Action,
    observation_from_state,
    read_smb1_state,
)
from fami_pixel.learning import build_rollout_record, write_jsonl_record
from fami_pixel.learning.hazard_sampling import (
    select_death_probe_candidates,
    select_depth_beam,
    select_hazard_branches,
)

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
        choices=("greedy", "hazard", "hazard-beam", "hazard-beam-probe"),
        default="greedy",
        help=(
            "greedy follows one best path; hazard expands breadth-first; "
            "hazard-beam keeps a fixed-depth frontier; hazard-beam-probe also "
            "extends selected survivors with a neutral death probe"
        ),
    )
    parser.add_argument(
        "--run-tag",
        default="",
        help=(
            "optional source-identity suffix for independent append runs "
            "(letters, digits, dot, underscore, hyphen only)"
        ),
    )
    parser.add_argument(
        "--branch-width",
        type=int,
        default=3,
        help="maximum children/beam width for hazard sampling modes",
    )
    parser.add_argument(
        "--death-probe-frames",
        type=int,
        default=48,
        help="neutral continuation horizon used by hazard-beam-probe",
    )
    parser.add_argument(
        "--death-probe-width",
        type=int,
        default=6,
        help="maximum surviving candidates probed per root",
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
    if args.death_probe_frames <= 0:
        parser.error("--death-probe-frames must be > 0")
    if args.death_probe_width <= 0:
        parser.error("--death-probe-width must be > 0")
    run_tag = str(args.run_tag).strip()
    if run_tag and any(not (ch.isalnum() or ch in "._-") for ch in run_tag):
        parser.error("--run-tag may contain only letters, digits, dot, underscore, and hyphen")
    args.run_tag = run_tag
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
        source = base_name
    elif args.sampling == "hazard":
        source = f"{base_name}-hazard"
    elif args.sampling == "hazard-beam":
        source = f"{base_name}-hazard-beam"
    else:
        source = f"{base_name}-hazard-beam-probe"
    if args.run_tag:
        source = f"{source}-{args.run_tag}"
    return source


def _checkpoint_prefix(args: argparse.Namespace) -> str:
    if not args.run_tag:
        return args.sampling
    return f"{args.sampling}-{args.run_tag}"


def _probe_settings(args: argparse.Namespace) -> tuple[int, int]:
    if args.sampling != "hazard-beam-probe":
        return 0, 0
    return int(args.death_probe_frames), int(args.death_probe_width)


def _run_neutral_probe(
    core,
    candidate,
    checkpoint: Path,
    root_frame: int,
    root_x: int,
    root_engine: int,
    *,
    probe_frames: int,
    step_timeout: float,
) -> str:
    """Replay one survivor, then observe a neutral continuation in Mesen."""
    base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
    candidate_start = observation_from_state(core.frame_count(), read_smb1_state(core))
    replay = v10.run_candidate_pit_aware(core, candidate, candidate_start, step_timeout)
    if replay.terminal != CandidateTerminal.NONE:
        return replay.terminal.value

    probe_start = observation_from_state(core.frame_count(), read_smb1_state(core))
    neutral = PlanCandidate(
        f"neutral_probe_{probe_frames}",
        (ActionCommand(Smb1Action.NOOP, probe_frames),),
    )
    probe = v10.run_candidate_pit_aware(core, neutral, probe_start, step_timeout)
    return probe.terminal.value


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
    death_probe_frames: int = 0,
    death_probe_width: int = 0,
):
    samples = []
    for candidate in candidates:
        base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
        candidate_start = observation_from_state(core.frame_count(), read_smb1_state(core))
        outcome = v10.run_candidate_pit_aware(core, candidate, candidate_start, step_timeout)
        end = observation_from_state(core.frame_count(), read_smb1_state(core))
        samples.append((outcome, candidate_start, end))

    probe_terminals: dict[str, str] = {}
    if death_probe_frames > 0 and death_probe_width > 0:
        selected = select_death_probe_candidates(
            (outcome for outcome, _start, _end in samples),
            death_probe_width,
        )
        for outcome in selected:
            probe_terminals[str(outcome.candidate.name)] = _run_neutral_probe(
                core,
                outcome.candidate,
                checkpoint,
                root_frame,
                root_x,
                root_engine,
                probe_frames=death_probe_frames,
                step_timeout=step_timeout,
            )

    evaluated = []
    for outcome, candidate_start, end in samples:
        name = str(outcome.candidate.name)
        probe_terminal = probe_terminals.get(name)
        record = build_rollout_record(
            candidate_start,
            end,
            outcome,
            source=source,
            generation=generation,
            probe_terminal=probe_terminal,
            probe_frames=death_probe_frames if probe_terminal is not None else None,
        )
        write_jsonl_record(output, record)
        evaluated.append(outcome)
    return evaluated


def _collect_greedy(core, args, candidates, checkpoint_dir: Path, output: Path, source: str, current):
    records = 0
    prefix = _checkpoint_prefix(args)
    for root_index in range(args.roots):
        checkpoint = checkpoint_dir / f"{prefix}-root-{root_index:04d}.mss"
        root_frame, root_x, root_engine = base.save_checkpoint(core, checkpoint)
        start = observation_from_state(core.frame_count(), read_smb1_state(core))
        evaluated = _evaluate_root(
            core, candidates, checkpoint, root_frame, root_x, root_engine,
            source=source, generation=root_index, output=output,
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
    prefix = _checkpoint_prefix(args)
    initial = checkpoint_dir / f"{prefix}-root-0000.mss"
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
            core, candidates, checkpoint, root_frame, root_x, root_engine,
            source=source, generation=root_index, output=output,
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
            child_path = checkpoint_dir / f"{prefix}-child-{root_index:04d}-{branch_index:02d}.mss"
            child_frame, child_x, child_engine = base.save_checkpoint(core, child_path)
            frontier.append((child_path, child_frame, child_x, child_engine))
            current = child
    return records, current


def _collect_hazard_beam(core, args, candidates, checkpoint_dir: Path, output: Path, source: str, current):
    prefix = _checkpoint_prefix(args)
    initial_path = checkpoint_dir / f"{prefix}-root-0000.mss"
    root_frame, root_x, root_engine = base.save_checkpoint(core, initial_path)
    beam = [_RootState(initial_path, root_frame, root_x, root_engine)]
    seen_states = {_state_signature(current)}
    records = 0
    root_index = 0
    depth = 0
    probe_frames, probe_width = _probe_settings(args)

    while beam and root_index < args.roots:
        expanded: list[_BeamChild] = []
        print(f"beam depth {depth:03d} roots={len(beam)}", flush=True)
        for beam_slot, root in enumerate(beam):
            if root_index >= args.roots:
                break
            base.restore_checkpoint(core, root.checkpoint, root.frame, root.x, root.engine)
            start = observation_from_state(core.frame_count(), read_smb1_state(core))
            evaluated = _evaluate_root(
                core, candidates, root.checkpoint, root.frame, root.x, root.engine,
                source=source, generation=root_index, output=output,
                step_timeout=args.step_timeout,
                death_probe_frames=probe_frames,
                death_probe_width=probe_width,
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
                    f"{prefix}-d{depth:03d}-r{root_index:04d}-b{branch_index:02d}.mss"
                )
                child_frame, child_x, child_engine = base.save_checkpoint(core, child_path)
                expanded.append(
                    _BeamChild(
                        root=_RootState(child_path, child_frame, child_x, child_engine),
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
    if args.run_tag:
        print(f"Run tag       : {args.run_tag}", flush=True)
    if args.sampling in ("hazard", "hazard-beam", "hazard-beam-probe"):
        print(f"Branch width  : {args.branch_width}", flush=True)
    if args.sampling == "hazard-beam-probe":
        print(f"Death probe   : {args.death_probe_width} candidates x {args.death_probe_frames} neutral frames", flush=True)
    print(f"Source        : {source}", flush=True)
    print(f"Dataset       : {output}", flush=True)
    print(f"Output mode   : {'append' if args.append else 'replace'}", flush=True)

    if args.sampling == "hazard":
        records, current = _collect_hazard(core, args, candidates, checkpoint_dir, output, source, current)
    elif args.sampling in ("hazard-beam", "hazard-beam-probe"):
        records, current = _collect_hazard_beam(core, args, candidates, checkpoint_dir, output, source, current)
    else:
        records, current = _collect_greedy(core, args, candidates, checkpoint_dir, output, source, current)

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