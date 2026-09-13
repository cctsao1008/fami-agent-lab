#!/usr/bin/env python3
"""Closed-loop rollout-tail checkpoint planner for SMB1 World 1-1.

V4 keeps the 30-frame committed control cadence from V3, but replaces V3's
RIGHT-only open-loop safety tail with a bounded closed-loop continuation.  For
each root candidate, the counterfactual tail periodically checkpoints its own
state, evaluates the same action vocabulary against authoritative Mesen, commits
the locally best tail action inside that counterfactual branch, and repeats.

Only the first 30-frame root candidate is ever committed to the real episode.
All tail transitions remain counterfactual planning evidence.
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-rate checkpoint planner with closed-loop rollout tails."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("build/checkpoints/smb1-planner-v4.mss"),
    )
    parser.add_argument("--max-decisions", type=int, default=160)
    parser.add_argument("--step-timeout", type=float, default=5.0)
    parser.add_argument(
        "--tail-decisions",
        type=int,
        default=4,
        help="Number of 30-frame closed-loop continuation decisions in each audit.",
    )
    parser.add_argument("--audit-interval", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=2400.0)
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
        outcomes.append(
            run_candidate(core, candidate, checkpoint_observation, timeout_s)
        )
    return tuple(outcomes)


def run_candidate_with_closed_loop_tail(
    core: MesenCore,
    candidate,
    start_observation,
    timeout_s: float,
    tail_decisions: int,
    tail_state_file: Path,
) -> tuple[CandidateOutcome, tuple[str, ...]]:
    """Evaluate one root macro followed by a bounded greedy checkpoint policy.

    The tail is deliberately model-free: every local alternative is executed
    against Mesen from a temporary checkpoint.  The locally selected tail action
    is committed only inside this counterfactual branch, then another local
    decision is made.  This makes the continuation closed-loop rather than the
    fixed RIGHT trajectory used in V3.
    """

    first = run_candidate(core, candidate, start_observation, timeout_s)
    trace: list[str] = []
    if (
        first.terminal != CandidateTerminal.NONE
        or first.reached_flagpole
        or tail_decisions <= 0
    ):
        return first, tuple(trace)

    total_elapsed = first.elapsed_frames
    max_x = first.max_x
    terminal = CandidateTerminal.NONE
    reached_flagpole = first.reached_flagpole
    final_x = first.end_x

    for tail_index in range(1, tail_decisions + 1):
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)
        final_x = current.mario_x_abs
        max_x = max(max_x, final_x)

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            reached_flagpole = True
            break

        tail_frame, tail_x, tail_engine = save_checkpoint(core, tail_state_file)
        tail_outcomes = evaluate_local_candidates(
            core,
            tail_state_file,
            tail_frame,
            tail_x,
            tail_engine,
            current,
            timeout_s,
        )
        best_tail = select_best_candidate(tail_outcomes)
        trace.append(
            f"t{tail_index}:{best_tail.candidate.name}/"
            f"dx={best_tail.progress}/term={best_tail.terminal.value}"
        )

        restore_checkpoint(
            core,
            tail_state_file,
            tail_frame,
            tail_x,
            tail_engine,
        )
        committed_tail = run_candidate(
            core,
            best_tail.candidate,
            current,
            timeout_s,
        )
        total_elapsed += committed_tail.elapsed_frames
        final_x = committed_tail.end_x
        max_x = max(max_x, committed_tail.max_x)
        terminal = committed_tail.terminal
        reached_flagpole = reached_flagpole or committed_tail.reached_flagpole

        if terminal != CandidateTerminal.NONE or reached_flagpole:
            break

    set_nes_controller_state(core, 0, 0x00)
    return (
        CandidateOutcome(
            candidate=candidate,
            start_x=start_observation.mario_x_abs,
            end_x=final_x,
            max_x=max_x,
            elapsed_frames=total_elapsed,
            terminal=terminal,
            reached_flagpole=reached_flagpole,
        ),
        tuple(trace),
    )


def evaluate_audit(
    core: MesenCore,
    root_state_file: Path,
    checkpoint_frame: int,
    checkpoint_x: int,
    checkpoint_engine: int,
    checkpoint_observation,
    timeout_s: float,
    tail_decisions: int,
) -> tuple[tuple[CandidateOutcome, tuple[str, ...]], ...]:
    results: list[tuple[CandidateOutcome, tuple[str, ...]]] = []
    tail_state_file = root_state_file.with_name(
        f"{root_state_file.stem}-tail{root_state_file.suffix}"
    )

    for candidate in CANDIDATES:
        restore_checkpoint(
            core,
            root_state_file,
            checkpoint_frame,
            checkpoint_x,
            checkpoint_engine,
        )
        results.append(
            run_candidate_with_closed_loop_tail(
                core,
                candidate,
                checkpoint_observation,
                timeout_s,
                tail_decisions,
                tail_state_file,
            )
        )
    return tuple(results)


def finish_success(
    core: MesenCore,
    current,
    episode: EpisodeAccumulator,
    timeout_s: float,
) -> None:
    if current.game_engine_subroutine == FLAGPOLE_SLIDE:
        current = finish_flagpole(core, current, episode, timeout_s)
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
    print(
        "PlannerV4 : PASS World 1-1 completion via closed-loop rollout-tail search",
        flush=True,
    )
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
        "PlannerV4 : 30-frame fast loop + closed-loop future-policy audit; "
        f"tail_decisions={args.tail_decisions} audit_interval={args.audit_interval}",
        flush=True,
    )

    for decision in range(1, args.max_decisions + 1):
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            finish_success(core, current, episode, args.step_timeout)

        checkpoint_frame, checkpoint_x, checkpoint_engine = save_checkpoint(
            core, state_file
        )
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
            audited_pairs = evaluate_audit(
                core,
                state_file,
                checkpoint_frame,
                checkpoint_x,
                checkpoint_engine,
                checkpoint_observation,
                args.step_timeout,
                args.tail_decisions,
            )
            audited = tuple(outcome for outcome, _ in audited_pairs)
            best = select_best_candidate(audited)
            print(
                f"Audit     : decision={decision:03d} frame={checkpoint_frame} "
                f"X={checkpoint_x} reason={reason} tail_decisions={args.tail_decisions}",
                flush=True,
            )
            for outcome, tail_trace in audited_pairs:
                trace_text = ",".join(tail_trace) if tail_trace else "-"
                print(
                    f"Future    : {outcome.candidate.name}:"
                    f"score={score_candidate(outcome):.3f},"
                    f"dx={outcome.progress},frames={outcome.elapsed_frames},"
                    f"term={outcome.terminal.value},flag={int(outcome.reached_flagpole)},"
                    f"max_x={outcome.max_x},tail=[{trace_text}]",
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
                f"PlannerV4 : FAIL selected rollout died frame={current.native_frame_id} "
                f"X={current.mario_x_abs} max_x={result.max_x}"
            )
        if terminal == CandidateTerminal.LEVEL_COMPLETE:
            finish_success(core, current, episode, args.step_timeout)
        if reached_flagpole:
            finish_success(core, current, episode, args.step_timeout)

    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(
        f"PlannerV4 : FAIL decision limit={args.max_decisions} "
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
        "--tail-decisions", str(args.tail_decisions),
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
