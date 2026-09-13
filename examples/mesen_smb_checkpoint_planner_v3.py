#!/usr/bin/env python3
"""Multi-rate rollout-tail checkpoint planner for SMB1 World 1-1.

V3 keeps the committed control cadence at one 30-frame action macro, but adds a
slower safety audit that rolls each candidate farther into the future using a
fixed RIGHT-only tail policy. This is intended to expose delayed consequences
(such as entering an unrecoverable fall whose DIED edge appears much later)
without hard-coded level coordinates and without exponential tree growth.
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    NES_RIGHT,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
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
    step,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-rate checkpoint planner with bounded future rollout audits."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("build/checkpoints/smb1-planner-v3.mss"),
    )
    parser.add_argument("--max-decisions", type=int, default=160)
    parser.add_argument("--step-timeout", type=float, default=5.0)
    parser.add_argument("--tail-frames", type=int, default=120)
    parser.add_argument("--audit-interval", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def fail(message: str) -> None:
    print(message, flush=True)
    print(FAIL_MARKER, flush=True)
    threading.Event().wait()


def should_audit(
    decision: int,
    outcomes: tuple[CandidateOutcome, ...],
    nominal_progress: int,
    audit_interval: int,
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
    if audit_interval > 0 and decision % audit_interval == 0:
        return True, "periodic-safety-audit"
    return False, "fast-loop"


def run_candidate_with_tail(
    core: MesenCore,
    candidate,
    start_observation,
    timeout_s: float,
    tail_frames: int,
) -> CandidateOutcome:
    """Evaluate one action macro plus a bounded RIGHT-only continuation.

    Only the first macro may later be committed. The tail is counterfactual and
    exists solely to expose delayed terminal/risk consequences that a 30-frame
    horizon cannot see.
    """

    immediate = run_candidate(core, candidate, start_observation, timeout_s)
    if (
        immediate.terminal != CandidateTerminal.NONE
        or immediate.reached_flagpole
        or tail_frames <= 0
    ):
        return immediate

    state = read_smb1_state(core)
    previous = observation_from_state(core.frame_count(), state)
    max_x = max(immediate.max_x, previous.mario_x_abs)
    elapsed = immediate.elapsed_frames
    terminal = CandidateTerminal.NONE
    reached_flagpole = False

    set_nes_controller_state(core, 0, NES_RIGHT)
    for _ in range(tail_frames):
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
        if terminal != CandidateTerminal.NONE or reached_flagpole:
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


def evaluate_audit(
    core: MesenCore,
    state_file: Path,
    checkpoint_frame: int,
    checkpoint_x: int,
    checkpoint_engine: int,
    checkpoint_observation,
    timeout_s: float,
    tail_frames: int,
) -> tuple[CandidateOutcome, ...]:
    results: list[CandidateOutcome] = []
    for candidate in CANDIDATES:
        restore_checkpoint(
            core,
            state_file,
            checkpoint_frame,
            checkpoint_x,
            checkpoint_engine,
        )
        results.append(
            run_candidate_with_tail(
                core,
                candidate,
                checkpoint_observation,
                timeout_s,
                tail_frames,
            )
        )
    return tuple(results)


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
    print("PlannerV3 : PASS World 1-1 completion via rollout-tail checkpoint search", flush=True)
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
        "PlannerV3 : 30-frame fast loop + periodic/triggered future rollout audit; "
        f"tail={args.tail_frames} audit_interval={args.audit_interval}",
        flush=True,
    )

    for decision in range(1, args.max_decisions + 1):
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            finish_success(core, current, episode, args.step_timeout)

        checkpoint_frame, checkpoint_x, checkpoint_engine = save_checkpoint(core, state_file)
        checkpoint_observation = current

        immediate: list[CandidateOutcome] = []
        for candidate in CANDIDATES:
            restore_checkpoint(
                core,
                state_file,
                checkpoint_frame,
                checkpoint_x,
                checkpoint_engine,
            )
            immediate.append(
                run_candidate(core, candidate, checkpoint_observation, args.step_timeout)
            )

        one_ply = tuple(immediate)
        best = select_best_candidate(one_ply)
        use_audit, reason = should_audit(
            decision,
            one_ply,
            nominal_progress,
            args.audit_interval,
        )

        if use_audit:
            audited = evaluate_audit(
                core,
                state_file,
                checkpoint_frame,
                checkpoint_x,
                checkpoint_engine,
                checkpoint_observation,
                args.step_timeout,
                args.tail_frames,
            )
            best = select_best_candidate(audited)
            print(
                f"Audit     : decision={decision:03d} frame={checkpoint_frame} "
                f"X={checkpoint_x} reason={reason} tail={args.tail_frames}",
                flush=True,
            )
            for outcome in audited:
                print(
                    f"Future    : {outcome.candidate.name}:score={score_candidate(outcome):.3f},"
                    f"dx={outcome.progress},frames={outcome.elapsed_frames},"
                    f"term={outcome.terminal.value},flag={int(outcome.reached_flagpole)},"
                    f"max_x={outcome.max_x}",
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
            f"choose={best.candidate.name} mode={'audit' if use_audit else 'fast'} | {summary}",
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
                f"PlannerV3 : FAIL selected rollout died frame={current.native_frame_id} "
                f"X={current.mario_x_abs} max_x={result.max_x}"
            )
        if terminal == CandidateTerminal.LEVEL_COMPLETE:
            finish_success(core, current, episode, args.step_timeout)
        if reached_flagpole:
            finish_success(core, current, episode, args.step_timeout)

    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(
        f"PlannerV3 : FAIL decision limit={args.max_decisions} "
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
        "--tail-frames", str(args.tail_frames),
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
