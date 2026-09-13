#!/usr/bin/env python3
"""Run a bounded receding-horizon SMB1 planner against authoritative Mesen.

Each decision saves one real Mesen state, evaluates several equal-horizon action
macros by restoring that checkpoint, ranks the observed outcomes, restores once
more, and commits only the selected candidate. Candidate rollouts never become
machine truth; the committed Mesen execution remains authoritative.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    NES_START,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    ActionCommand,
    CandidateOutcome,
    CandidateTerminal,
    EpisodeAccumulator,
    EpisodeTermination,
    GameEventType,
    PlanCandidate,
    Smb1Action,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
    score_candidate,
    select_best_candidate,
)

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"
PLAYER_CONTROL = 0x08
FLAGPOLE_SLIDE = 0x04

CANDIDATES = (
    PlanCandidate("cruise", (ActionCommand(Smb1Action.RIGHT, 30),)),
    PlanCandidate(
        "tap_jump",
        (
            ActionCommand(Smb1Action.RIGHT_A, 6),
            ActionCommand(Smb1Action.RIGHT, 24),
        ),
    ),
    PlanCandidate(
        "medium_jump",
        (
            ActionCommand(Smb1Action.RIGHT_A, 14),
            ActionCommand(Smb1Action.RIGHT, 16),
        ),
    ),
    PlanCandidate(
        "long_jump",
        (
            ActionCommand(Smb1Action.RIGHT_A, 24),
            ActionCommand(Smb1Action.RIGHT, 6),
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bounded checkpoint action search for SMB1 World 1-1."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("build/checkpoints/smb1-planner.mss"),
    )
    parser.add_argument("--max-decisions", type=int, default=160)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, flush=True)
    print(FAIL_MARKER, flush=True)
    threading.Event().wait()


def step(core: MesenCore, timeout_s: float) -> None:
    core.step_frame_sync(1, max(1, int(timeout_s * 1000)))


def enter_world_1_1(core: MesenCore, timeout_s: float):
    set_nes_controller_state(core, 0, 0x00)
    for _ in range(300):
        step(core, timeout_s)
        state = read_smb1_state(core)
        if state.is_title_menu:
            break
    else:
        fail("TitleMenu : FAIL")

    print(f"TitleMenu : PASS NativeFrame={core.frame_count()}", flush=True)
    set_nes_controller_state(core, 0, NES_START)
    step(core, timeout_s)
    set_nes_controller_state(core, 0, 0x00)
    step(core, timeout_s)

    for _ in range(360):
        step(core, timeout_s)
        state = read_smb1_state(core)
        if state.is_world_1_1_player_control:
            print(
                f"GameEntry : PASS NativeFrame={core.frame_count()} X={state.player_absolute_x}",
                flush=True,
            )
            return state
    fail("GameEntry : FAIL")


def save_checkpoint(core: MesenCore, path: Path) -> tuple[int, int, int]:
    set_nes_controller_state(core, 0, 0x00)
    state = read_smb1_state(core)
    frame_id = core.frame_count()
    if path.exists():
        path.unlink()
    core.save_state_file(path)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return frame_id, state.player_absolute_x, state.game_engine_subroutine
        time.sleep(0.005)
    fail(f"Checkpoint: FAIL save did not materialize: {path}")
    raise AssertionError("unreachable")


def restore_checkpoint(
    core: MesenCore,
    path: Path,
    expected_frame: int,
    expected_x: int,
    expected_engine: int,
) -> None:
    set_nes_controller_state(core, 0, 0x00)
    core.load_state_file(path)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        state = read_smb1_state(core)
        if (
            core.frame_count() == expected_frame
            and state.player_absolute_x == expected_x
            and state.game_engine_subroutine == expected_engine
        ):
            return
        time.sleep(0.005)
    state = read_smb1_state(core)
    fail(
        "Restore   : FAIL "
        f"expected=({expected_frame},{expected_x},0x{expected_engine:02X}) "
        f"actual=({core.frame_count()},{state.player_absolute_x},"
        f"0x{state.game_engine_subroutine:02X})"
    )


def run_candidate(
    core: MesenCore,
    candidate: PlanCandidate,
    start_observation,
    timeout_s: float,
) -> CandidateOutcome:
    previous = start_observation
    max_x = previous.mario_x_abs
    terminal = CandidateTerminal.NONE
    reached_flagpole = previous.game_engine_subroutine == FLAGPOLE_SLIDE
    elapsed = 0

    for command in candidate.commands:
        set_nes_controller_state(core, 0, command.nes_buttons)
        for _ in range(command.frame_count):
            step(core, timeout_s)
            elapsed += 1
            state = read_smb1_state(core)
            current = observation_from_state(core.frame_count(), state)
            max_x = max(max_x, current.mario_x_abs)
            reached_flagpole = reached_flagpole or (
                current.game_engine_subroutine == FLAGPOLE_SLIDE
            )
            events = derive_game_events(previous, current)
            if any(event.kind == GameEventType.LEVEL_COMPLETED for event in events):
                terminal = CandidateTerminal.LEVEL_COMPLETE
            elif any(event.kind == GameEventType.DIED for event in events):
                terminal = CandidateTerminal.DEATH
            previous = current
            if terminal != CandidateTerminal.NONE:
                break
        if terminal != CandidateTerminal.NONE:
            break

    set_nes_controller_state(core, 0, 0x00)
    return CandidateOutcome(
        candidate=candidate,
        start_x=start_observation.mario_x_abs,
        end_x=previous.mario_x_abs,
        max_x=max_x,
        elapsed_frames=elapsed,
        terminal=terminal,
        reached_flagpole=reached_flagpole,
    )


def commit_candidate(
    core: MesenCore,
    candidate: PlanCandidate,
    start_observation,
    episode: EpisodeAccumulator,
    timeout_s: float,
):
    previous = start_observation
    reached_flagpole = previous.game_engine_subroutine == FLAGPOLE_SLIDE
    terminal = CandidateTerminal.NONE

    for command in candidate.commands:
        set_nes_controller_state(core, 0, command.nes_buttons)
        for _ in range(command.frame_count):
            step(core, timeout_s)
            state = read_smb1_state(core)
            current = observation_from_state(core.frame_count(), state)
            events = derive_game_events(previous, current)
            episode.record(current, events)
            reached_flagpole = reached_flagpole or (
                current.game_engine_subroutine == FLAGPOLE_SLIDE
            )
            if any(event.kind == GameEventType.LEVEL_COMPLETED for event in events):
                terminal = CandidateTerminal.LEVEL_COMPLETE
            elif any(event.kind == GameEventType.DIED for event in events):
                terminal = CandidateTerminal.DEATH
            previous = current
            if terminal != CandidateTerminal.NONE:
                break
        if terminal != CandidateTerminal.NONE:
            break

    set_nes_controller_state(core, 0, 0x00)
    return previous, terminal, reached_flagpole


def finish_flagpole(core: MesenCore, previous, episode: EpisodeAccumulator, timeout_s: float):
    """Let the authoritative flagpole sequence run until PlayerEndLevel."""

    set_nes_controller_state(core, 0, 0x00)
    for _ in range(600):
        step(core, timeout_s)
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)
        events = derive_game_events(previous, current)
        episode.record(current, events)
        if any(event.kind == GameEventType.LEVEL_COMPLETED for event in events):
            return current
        if any(event.kind == GameEventType.DIED for event in events):
            fail(f"Flagpole  : FAIL death during completion sequence frame={current.native_frame_id}")
        previous = current
    fail("Flagpole  : FAIL no LEVEL_COMPLETED within 600 frames")
    raise AssertionError("unreachable")


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

    print(
        "Planner   : bounded checkpoint search; candidates="
        + ",".join(candidate.name for candidate in CANDIDATES),
        flush=True,
    )

    for decision in range(1, args.max_decisions + 1):
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            completed = finish_flagpole(core, current, episode, args.step_timeout)
            result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
            print(
                f"LevelEdge : PASS frame={completed.native_frame_id} X={completed.mario_x_abs}",
                flush=True,
            )
            print(
                f"Episode   : PASS termination={result.termination.value} "
                f"frames={result.elapsed_frames} max_x={result.max_x} "
                f"progress={result.net_progress} events={result.event_count}",
                flush=True,
            )
            print("Planner   : PASS World 1-1 completion via checkpoint search", flush=True)
            print(READY_MARKER, flush=True)
            threading.Event().wait()

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
            outcome = run_candidate(
                core,
                candidate,
                checkpoint_observation,
                args.step_timeout,
            )
            outcomes.append(outcome)

        best = select_best_candidate(tuple(outcomes))
        summary = " ".join(
            f"{outcome.candidate.name}:score={score_candidate(outcome):.3f},"
            f"dx={outcome.progress},term={outcome.terminal.value},"
            f"flag={int(outcome.reached_flagpole)}"
            for outcome in outcomes
        )
        print(
            f"Decision  : {decision:03d} frame={checkpoint_frame} X={checkpoint_x} "
            f"choose={best.candidate.name} | {summary}",
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
                f"Planner   : FAIL selected rollout died frame={current.native_frame_id} "
                f"X={current.mario_x_abs} max_x={result.max_x}"
            )

        if terminal == CandidateTerminal.LEVEL_COMPLETE:
            result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
            print(
                f"LevelEdge : PASS frame={current.native_frame_id} X={current.mario_x_abs}",
                flush=True,
            )
            print(
                f"Episode   : PASS termination={result.termination.value} "
                f"frames={result.elapsed_frames} max_x={result.max_x} "
                f"progress={result.net_progress} events={result.event_count}",
                flush=True,
            )
            print("Planner   : PASS World 1-1 completion via checkpoint search", flush=True)
            print(READY_MARKER, flush=True)
            threading.Event().wait()

        if reached_flagpole:
            completed = finish_flagpole(core, current, episode, args.step_timeout)
            result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
            print(
                f"LevelEdge : PASS frame={completed.native_frame_id} X={completed.mario_x_abs}",
                flush=True,
            )
            print(
                f"Episode   : PASS termination={result.termination.value} "
                f"frames={result.elapsed_frames} max_x={result.max_x} "
                f"progress={result.net_progress} events={result.event_count}",
                flush=True,
            )
            print("Planner   : PASS World 1-1 completion via checkpoint search", flush=True)
            print(READY_MARKER, flush=True)
            threading.Event().wait()

    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(
        f"Planner   : FAIL decision limit={args.max_decisions} "
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
