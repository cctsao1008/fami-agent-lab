#!/usr/bin/env python3
"""State-diverse beam checkpoint planner for SMB1 World 1-1.

V5 addresses the state-aliasing exposed by V4.  Equal X progress is not treated
as an equal future state: beam nodes retain Mario motion state (Y, player state,
X/Y speed and engine routine) and several distinct checkpoint branches are kept
alive across a bounded multi-decision audit.

Only the first 30-frame root candidate selected by the audit is committed to the
real episode.  Every deeper branch remains counterfactual planning evidence.
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    CandidateOutcome,
    CandidateTerminal,
    EpisodeAccumulator,
    EpisodeTermination,
    observation_from_state,
    read_smb1_state,
    score_candidate,
    select_best_candidate,
)

from mesen_smb_checkpoint_planner import (
    CANDIDATES,
    FLAGPOLE_SLIDE,
    READY_MARKER,
    FAIL_MARKER,
    commit_candidate,
    enter_world_1_1,
    finish_flagpole,
    restore_checkpoint,
    run_candidate,
    save_checkpoint,
)
from mesen_smb_checkpoint_planner_v3 import should_audit


@dataclass(frozen=True)
class BeamNode:
    root_candidate_name: str
    checkpoint_path: Path
    observation: object
    start_x: int
    max_x: int
    elapsed_frames: int
    terminal: CandidateTerminal
    reached_flagpole: bool
    trace: tuple[str, ...]

    @property
    def progress(self) -> int:
        return self.observation.mario_x_abs - self.start_x

    @property
    def state_signature(self) -> tuple[int, ...]:
        obs = self.observation
        return (
            obs.mario_x_abs,
            obs.mario_y,
            obs.mario_y_high,
            obs.player_state,
            obs.player_x_speed,
            obs.player_y_speed,
            obs.game_engine_subroutine,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="State-diverse beam checkpoint planner for SMB1 World 1-1."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("build/checkpoints/smb1-planner-v5.mss"),
    )
    parser.add_argument("--max-decisions", type=int, default=160)
    parser.add_argument("--step-timeout", type=float, default=5.0)
    parser.add_argument("--beam-depth", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--audit-interval", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, flush=True)
    print(FAIL_MARKER, flush=True)
    threading.Event().wait()


def evaluate_local_candidates(
    core: MesenCore,
    checkpoint_path: Path,
    checkpoint_frame: int,
    checkpoint_x: int,
    checkpoint_engine: int,
    checkpoint_observation,
    timeout_s: float,
) -> tuple[CandidateOutcome, ...]:
    outcomes: list[CandidateOutcome] = []
    for candidate in CANDIDATES:
        restore_checkpoint(
            core,
            checkpoint_path,
            checkpoint_frame,
            checkpoint_x,
            checkpoint_engine,
        )
        outcomes.append(run_candidate(core, candidate, checkpoint_observation, timeout_s))
    return tuple(outcomes)


def node_score(node: BeamNode) -> float:
    if node.terminal == CandidateTerminal.LEVEL_COMPLETE:
        return 1_000_000.0
    if node.terminal == CandidateTerminal.DEATH:
        return -1_000_000.0
    if node.reached_flagpole:
        return 100_000.0 + node.progress
    return node.progress / max(1, node.elapsed_frames)


def select_diverse_beam(nodes: list[BeamNode], beam_width: int) -> list[BeamNode]:
    """Keep the strongest unique machine-state signatures.

    This intentionally does not collapse branches merely because they reached the
    same X coordinate.  Distinct vertical/motion states remain separate search
    hypotheses.
    """

    ordered = sorted(
        nodes,
        key=lambda node: (
            node_score(node),
            node.progress,
            node.max_x,
        ),
        reverse=True,
    )
    selected: list[BeamNode] = []
    seen: set[tuple[int, ...]] = set()
    for node in ordered:
        signature = node.state_signature
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(node)
        if len(selected) >= beam_width:
            break
    return selected


def save_node_checkpoint(core: MesenCore, path: Path) -> tuple[object, int, int, int]:
    frame, x, engine = save_checkpoint(core, path)
    state = read_smb1_state(core)
    observation = observation_from_state(frame, state)
    return observation, frame, x, engine


def beam_audit(
    core: MesenCore,
    root_state_file: Path,
    root_frame: int,
    root_x: int,
    root_engine: int,
    root_observation,
    timeout_s: float,
    beam_depth: int,
    beam_width: int,
) -> tuple[str, tuple[str, ...]]:
    """Search a bounded, state-diverse checkpoint beam and return root action.

    Depth 1 always evaluates all four root candidates.  Later depths expand every
    retained beam node with the same four action macros, deduplicate exact motion
    signatures, and keep only the strongest bounded frontier.
    """

    audit_dir = root_state_file.parent / "v5-beam"
    if audit_dir.exists():
        shutil.rmtree(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)

    frontier: list[BeamNode] = []
    counter = 0
    root_order = {candidate.name: index for index, candidate in enumerate(CANDIDATES)}

    for candidate in CANDIDATES:
        restore_checkpoint(core, root_state_file, root_frame, root_x, root_engine)
        outcome = run_candidate(core, candidate, root_observation, timeout_s)
        state = read_smb1_state(core)
        observation = observation_from_state(core.frame_count(), state)
        counter += 1
        path = audit_dir / f"d01-n{counter:03d}.mss"
        if outcome.terminal == CandidateTerminal.NONE and not outcome.reached_flagpole:
            save_checkpoint(core, path)
        frontier.append(
            BeamNode(
                root_candidate_name=candidate.name,
                checkpoint_path=path,
                observation=observation,
                start_x=root_observation.mario_x_abs,
                max_x=outcome.max_x,
                elapsed_frames=outcome.elapsed_frames,
                terminal=outcome.terminal,
                reached_flagpole=outcome.reached_flagpole,
                trace=(candidate.name,),
            )
        )

    # Keep all distinct root candidates at depth 1 so a tie cannot erase an
    # alternative root action before its future state has been tested.
    frontier = select_diverse_beam(frontier, max(beam_width, len(CANDIDATES)))
    log_lines: list[str] = []
    log_lines.append(
        "Beam      : depth=1 "
        + " | ".join(
            f"root={n.root_candidate_name},x={n.observation.mario_x_abs},"
            f"y={n.observation.mario_y},ps={n.observation.player_state},"
            f"xs={n.observation.player_x_speed},ys={n.observation.player_y_speed},"
            f"score={node_score(n):.3f},term={n.terminal.value}"
            for n in frontier
        )
    )

    for depth in range(2, beam_depth + 1):
        if not frontier:
            break
        if any(
            node.terminal == CandidateTerminal.LEVEL_COMPLETE or node.reached_flagpole
            for node in frontier
        ):
            break

        children: list[BeamNode] = []
        for node in frontier:
            if node.terminal != CandidateTerminal.NONE or node.reached_flagpole:
                children.append(node)
                continue

            obs = node.observation
            restore_checkpoint(
                core,
                node.checkpoint_path,
                obs.native_frame_id,
                obs.mario_x_abs,
                obs.game_engine_subroutine,
            )

            for candidate in CANDIDATES:
                restore_checkpoint(
                    core,
                    node.checkpoint_path,
                    obs.native_frame_id,
                    obs.mario_x_abs,
                    obs.game_engine_subroutine,
                )
                outcome = run_candidate(core, candidate, obs, timeout_s)
                state = read_smb1_state(core)
                child_observation = observation_from_state(core.frame_count(), state)
                counter += 1
                child_path = audit_dir / f"d{depth:02d}-n{counter:03d}.mss"
                if outcome.terminal == CandidateTerminal.NONE and not outcome.reached_flagpole:
                    save_checkpoint(core, child_path)
                children.append(
                    BeamNode(
                        root_candidate_name=node.root_candidate_name,
                        checkpoint_path=child_path,
                        observation=child_observation,
                        start_x=node.start_x,
                        max_x=max(node.max_x, outcome.max_x),
                        elapsed_frames=node.elapsed_frames + outcome.elapsed_frames,
                        terminal=outcome.terminal,
                        reached_flagpole=node.reached_flagpole or outcome.reached_flagpole,
                        trace=node.trace + (candidate.name,),
                    )
                )

        frontier = select_diverse_beam(children, beam_width)
        log_lines.append(
            f"Beam      : depth={depth} "
            + " | ".join(
                f"root={n.root_candidate_name},x={n.observation.mario_x_abs},"
                f"y={n.observation.mario_y},ps={n.observation.player_state},"
                f"xs={n.observation.player_x_speed},ys={n.observation.player_y_speed},"
                f"score={node_score(n):.3f},term={n.terminal.value},"
                f"path={'>' .join(n.trace)}"
                for n in frontier
            )
        )

    if not frontier:
        return CANDIDATES[0].name, tuple(log_lines)

    best = max(
        frontier,
        key=lambda node: (
            node_score(node),
            node.progress,
            node.max_x,
            -root_order[node.root_candidate_name],
        ),
    )
    log_lines.append(
        f"BeamPick  : root={best.root_candidate_name} score={node_score(best):.3f} "
        f"x={best.observation.mario_x_abs} max_x={best.max_x} "
        f"term={best.terminal.value} path={'>'.join(best.trace)}"
    )
    return best.root_candidate_name, tuple(log_lines)


def finish_success(core: MesenCore, current, episode: EpisodeAccumulator, timeout_s: float) -> None:
    if current.game_engine_subroutine == FLAGPOLE_SLIDE:
        current = finish_flagpole(core, current, episode, timeout_s)
    result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
    print(f"LevelEdge : PASS frame={current.native_frame_id} X={current.mario_x_abs}", flush=True)
    print(
        f"Episode   : PASS termination={result.termination.value} "
        f"frames={result.elapsed_frames} max_x={result.max_x} "
        f"progress={result.net_progress} events={result.event_count}",
        flush=True,
    )
    print("PlannerV5 : PASS World 1-1 completion via state-diverse beam search", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def worker(args: argparse.Namespace) -> None:
    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)

    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)
    config = configure_standard_nes_controller(core, port=1)
    print(
        f"NesConfig : PASS (sizeof={ctypes.sizeof(config)} Port1.Type={config.Port1.Type})",
        flush=True,
    )
    if not core.load_rom(args.rom):
        fail("LoadRom   : FAIL")
    print("LoadRom   : PASS", flush=True)
    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)

    gameplay = enter_world_1_1(core, args.step_timeout)
    current = observation_from_state(core.frame_count(), gameplay)
    episode = EpisodeAccumulator(current)
    state_file = args.state_file.expanduser().resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    nominal_progress = 0

    print(
        "PlannerV5 : 30-frame fast loop + state-diverse beam audit; "
        f"beam_depth={args.beam_depth} beam_width={args.beam_width} "
        f"audit_interval={args.audit_interval}",
        flush=True,
    )

    for decision in range(1, args.max_decisions + 1):
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            finish_success(core, current, episode, args.step_timeout)

        checkpoint_frame, checkpoint_x, checkpoint_engine = save_checkpoint(core, state_file)
        checkpoint_observation = current

        one_ply = evaluate_local_candidates(
            core,
            state_file,
            checkpoint_frame,
            checkpoint_x,
            checkpoint_engine,
            checkpoint_observation,
            args.step_timeout,
        )
        best = select_best_candidate(one_ply)
        use_audit, reason = should_audit(
            decision,
            one_ply,
            nominal_progress,
            args.audit_interval,
        )

        if use_audit:
            root_name, beam_log = beam_audit(
                core,
                state_file,
                checkpoint_frame,
                checkpoint_x,
                checkpoint_engine,
                checkpoint_observation,
                args.step_timeout,
                args.beam_depth,
                args.beam_width,
            )
            for line in beam_log:
                print(line, flush=True)
            best = next(outcome for outcome in one_ply if outcome.candidate.name == root_name)
            print(
                f"Audit     : decision={decision:03d} frame={checkpoint_frame} X={checkpoint_x} "
                f"reason={reason} depth={args.beam_depth} width={args.beam_width}",
                flush=True,
            )

        safe_progress = max(
            (o.progress for o in one_ply if o.terminal == CandidateTerminal.NONE),
            default=0,
        )
        nominal_progress = max(nominal_progress, safe_progress)

        summary = " ".join(
            f"{o.candidate.name}:score={score_candidate(o):.3f},"
            f"dx={o.progress},term={o.terminal.value},flag={int(o.reached_flagpole)}"
            for o in one_ply
        )
        print(
            f"Decision  : {decision:03d} frame={checkpoint_frame} X={checkpoint_x} "
            f"choose={best.candidate.name} mode={'beam' if use_audit else 'fast'} | {summary}",
            flush=True,
        )

        restore_checkpoint(core, state_file, checkpoint_frame, checkpoint_x, checkpoint_engine)
        current, terminal, reached_flagpole = commit_candidate(
            core,
            best.candidate,
            checkpoint_observation,
            episode,
            args.step_timeout,
        )

        if terminal == CandidateTerminal.DEATH:
            result = episode.finish(EpisodeTermination.DEATH)
            fail(
                f"PlannerV5 : FAIL selected rollout died frame={current.native_frame_id} "
                f"X={current.mario_x_abs} max_x={result.max_x}"
            )
        if terminal == CandidateTerminal.LEVEL_COMPLETE:
            finish_success(core, current, episode, args.step_timeout)
        if reached_flagpole:
            finish_success(core, current, episode, args.step_timeout)

    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(
        f"PlannerV5 : FAIL decision limit={args.max_decisions} "
        f"frame={current.native_frame_id} X={current.mario_x_abs} max_x={result.max_x}"
    )


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--home", str(args.home),
        "--state-file", str(args.state_file),
        "--max-decisions", str(args.max_decisions),
        "--step-timeout", str(args.step_timeout),
        "--beam-depth", str(args.beam_depth),
        "--beam-width", str(args.beam_width),
        "--audit-interval", str(args.audit_interval),
        "--timeout", str(args.timeout),
        "--worker",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    lines: queue.Queue[str | None] = queue.Queue()
    assert process.stdout is not None

    def reader() -> None:
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    ready = False
    failed = False
    stream_closed = False
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            try:
                item = lines.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and stream_closed:
                    break
                continue

            if item is None:
                stream_closed = True
                if process.poll() is not None:
                    break
                continue

            print(item, end="")
            if READY_MARKER in item:
                ready = True
                break
            if FAIL_MARKER in item:
                failed = True
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

    if ready:
        print("WorkerExit: FORCED (expected containment)")
        print("Supervisor: PASS")
        return 0
    if failed:
        print("WorkerExit: FORCED (failed probe contained)")
        print("Supervisor: FAIL")
        return 6

    code = process.returncode
    print(f"Supervisor: FAIL (worker exit code {code})")
    return code if code not in (None, 0) else 3


def main() -> int:
    args = parse_args()
    if args.worker:
        worker(args)
        return 0
    return supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
