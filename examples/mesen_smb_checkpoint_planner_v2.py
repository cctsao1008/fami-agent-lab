#!/usr/bin/env python3
"""Adaptive two-ply checkpoint planner for SMB1 World 1-1.

This experiment keeps the first planner's authoritative Mesen rollout boundary,
but adds one extra ply only when one-ply search is ambiguous and degraded versus
previously observed safe progress. This avoids hard-coded X hazard windows while
allowing the planner to distinguish actions whose immediate progress is equal but
whose future states are not.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore, NES_START, configure_standard_nes_controller, set_nes_controller_state
from fami_pixel.games.smb1 import (
    CandidateOutcome,
    CandidateTerminal,
    EpisodeAccumulator,
    EpisodeTermination,
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
    score_candidate,
    select_best_candidate,
)

# Reuse the already validated one-ply candidate vocabulary and rollout helpers.
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adaptive two-ply checkpoint search for SMB1 World 1-1."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("build/checkpoints/smb1-planner-v2.mss"),
    )
    parser.add_argument("--max-decisions", type=int, default=160)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, flush=True)
    print(FAIL_MARKER, flush=True)
    threading.Event().wait()


def branch_score(first: CandidateOutcome, second: CandidateOutcome | None) -> float:
    """Score a first action by the best real continuation reachable from it."""

    if first.terminal == CandidateTerminal.LEVEL_COMPLETE:
        return 2_000_000.0
    if first.terminal == CandidateTerminal.DEATH:
        return -2_000_000.0
    if second is None:
        return score_candidate(first)
    if second.terminal == CandidateTerminal.LEVEL_COMPLETE:
        return 1_500_000.0
    if second.terminal == CandidateTerminal.DEATH:
        return -1_500_000.0

    combined_progress = second.end_x - first.start_x
    if first.reached_flagpole or second.reached_flagpole:
        return 500_000.0 + combined_progress
    combined_frames = first.elapsed_frames + second.elapsed_frames
    return combined_progress / combined_frames


def should_use_two_ply(
    outcomes: tuple[CandidateOutcome, ...],
    nominal_progress: int,
) -> tuple[bool, str]:
    best = select_best_candidate(outcomes)
    best_score = score_candidate(best)
    tied = sum(
        1
        for outcome in outcomes
        if score_candidate(outcome) == best_score and outcome.progress == best.progress
    )

    if best.progress <= 0:
        return True, "no-progress"
    if nominal_progress > 0 and best.progress < nominal_progress and tied >= 2:
        return True, "tied-degraded"
    return False, "one-ply-sufficient"


def evaluate_two_ply(
    core: MesenCore,
    root_path: Path,
    root_frame: int,
    root_x: int,
    root_engine: int,
    root_observation,
    first_outcomes: tuple[CandidateOutcome, ...],
    timeout_s: float,
):
    ranked: list[tuple[float, CandidateOutcome, CandidateOutcome | None]] = []

    for first in first_outcomes:
        if first.terminal != CandidateTerminal.NONE:
            ranked.append((branch_score(first, None), first, None))
            continue

        restore_checkpoint(core, root_path, root_frame, root_x, root_engine)
        replayed = run_candidate(core, first.candidate, root_observation, timeout_s)
        if replayed.terminal != CandidateTerminal.NONE:
            ranked.append((branch_score(replayed, None), replayed, None))
            continue

        branch_state = read_smb1_state(core)
        branch_observation = observation_from_state(core.frame_count(), branch_state)
        branch_path = root_path.with_name(
            f"{root_path.stem}-{first.candidate.name}-branch{root_path.suffix}"
        )
        branch_frame, branch_x, branch_engine = save_checkpoint(core, branch_path)

        continuations: list[CandidateOutcome] = []
        for second_candidate in CANDIDATES:
            restore_checkpoint(
                core,
                branch_path,
                branch_frame,
                branch_x,
                branch_engine,
            )
            continuations.append(
                run_candidate(
                    core,
                    second_candidate,
                    branch_observation,
                    timeout_s,
                )
            )

        best_second = select_best_candidate(tuple(continuations))
        ranked.append((branch_score(replayed, best_second), replayed, best_second))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


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
    print("PlannerV2 : PASS World 1-1 completion via adaptive checkpoint search", flush=True)
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
        "PlannerV2 : adaptive 1-ply/2-ply checkpoint search; candidates="
        + ",".join(candidate.name for candidate in CANDIDATES),
        flush=True,
    )

    for decision in range(1, args.max_decisions + 1):
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            finish_success(core, current, episode, args.step_timeout)

        checkpoint_frame, checkpoint_x, checkpoint_engine = save_checkpoint(core, state_file)
        checkpoint_observation = current
        outcomes: list[CandidateOutcome] = []

        for candidate in CANDIDATES:
            restore_checkpoint(
                core,
                state_file,
                checkpoint_frame,
                checkpoint_x,
                checkpoint_engine,
            )
            outcomes.append(
                run_candidate(
                    core,
                    candidate,
                    checkpoint_observation,
                    args.step_timeout,
                )
            )

        one_ply = tuple(outcomes)
        best = select_best_candidate(one_ply)
        use_two_ply, reason = should_use_two_ply(one_ply, nominal_progress)

        if use_two_ply:
            print(
                f"Lookahead : decision={decision:03d} frame={checkpoint_frame} "
                f"X={checkpoint_x} reason={reason} one_ply_dx={best.progress} "
                f"nominal_dx={nominal_progress}",
                flush=True,
            )
            ranked = evaluate_two_ply(
                core,
                state_file,
                checkpoint_frame,
                checkpoint_x,
                checkpoint_engine,
                checkpoint_observation,
                one_ply,
                args.step_timeout,
            )
            for score, first, second in ranked:
                continuation = second.candidate.name if second is not None else "-"
                second_dx = second.progress if second is not None else 0
                second_term = second.terminal.value if second is not None else "-"
                print(
                    f"Branch    : first={first.candidate.name} second={continuation} "
                    f"score={score:.3f} dx1={first.progress} dx2={second_dx} "
                    f"term1={first.terminal.value} term2={second_term}",
                    flush=True,
                )
            best = ranked[0][1]

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
            f"choose={best.candidate.name} depth={2 if use_two_ply else 1} | {summary}",
            flush=True,
        )

        restore_checkpoint(
            core,
            state_file,
            checkpoint_frame,
            checkpoint_x,
            checkpoint_engine,
        )
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
                f"PlannerV2 : FAIL selected rollout died frame={current.native_frame_id} "
                f"X={current.mario_x_abs} max_x={result.max_x}"
            )
        if terminal == CandidateTerminal.LEVEL_COMPLETE:
            finish_success(core, current, episode, args.step_timeout)
        if reached_flagpole:
            finish_success(core, current, episode, args.step_timeout)

    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(
        f"PlannerV2 : FAIL decision limit={args.max_decisions} "
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

    ready = False
    failed = False
    deadline = time.monotonic() + args.timeout
    assert process.stdout is not None
    try:
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                print(line, end="")
                if READY_MARKER in line:
                    ready = True
                    break
                if FAIL_MARKER in line:
                    failed = True
                    break
                continue
            if process.poll() is not None:
                break
            time.sleep(0.01)
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
