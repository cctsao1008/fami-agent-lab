#!/usr/bin/env python3
"""V10 gap-aware wrapper around V9 obstacle recovery.

V9 can visually recognize an already-doomed pit fall, but its counterfactual
candidate scorer still treats early pit entry as ordinary forward progress until
SMB1 reaches the authoritative DIED engine edge. V10 keeps V9's obstacle
recovery and adds a planning-only pit-risk predicate to candidate rollouts.

Important boundary:
- authoritative DIED remains derived only from SMB1 engine transitions,
- counterfactual candidates may be rejected earlier as planning hazards,
- authoritative execution is never relabeled as DIED without machine evidence.
"""

from __future__ import annotations

from datetime import datetime
import os
import queue
import subprocess
import sys
import threading
import time

from fami_pixel.games.smb1 import (
    CandidateOutcome,
    CandidateTerminal,
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)

import mesen_smb_checkpoint_planner_v6 as v6
import mesen_smb_checkpoint_planner_v7 as v7
import mesen_smb_checkpoint_planner_v8 as v8
import mesen_smb_checkpoint_planner_v9 as v9


# World 1-1 machine evidence: ordinary floor settles around Y=176.  A candidate
# that has already moved below that floor envelope while still descending is a
# poor action even if SMB1 has not yet entered PlayerLoseLife/PlayerDeath.
PLANNING_PIT_RISK_Y = 178
_RESULT_PREFIX = "PlannerV7:"
_V10_RESULT_PREFIX = "PlannerV10:"
_WORKER_ENV = "FAMI_PIXEL_V10_WORKER"
_NO_OUTPUT_TIMEOUT_S = 180.0
_POST_RESULT_GRACE_S = 1.0


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def _candidate_pit_risk(observation) -> bool:
    """Planning-only hazard; this is deliberately not the durable DIED event."""
    return (
        observation.game_engine_subroutine == v8.base.PLAYER_CONTROL
        and observation.mario_y >= PLANNING_PIT_RISK_Y
        and _signed_byte(observation.player_y_speed) > 0
    )


def run_candidate_pit_aware(core, candidate, start_observation, timeout_s):
    """Evaluate one candidate and reject a descent below the floor envelope.

    CandidateTerminal.DEATH here means "do not select this rollout" inside the
    planner.  It does not emit GameEventType.DIED and does not alter authoritative
    episode semantics.
    """
    previous = start_observation
    max_x = previous.mario_x_abs
    terminal = CandidateTerminal.NONE
    reached_flagpole = previous.game_engine_subroutine == v8.base.FLAGPOLE_SLIDE
    elapsed = 0

    for command in candidate.commands:
        v8.base.set_nes_controller_state(core, 0, command.nes_buttons)
        for _ in range(command.frame_count):
            v8.base.step(core, timeout_s)
            elapsed += 1
            state = read_smb1_state(core)
            current = observation_from_state(core.frame_count(), state)
            max_x = max(max_x, current.mario_x_abs)
            reached_flagpole = reached_flagpole or (
                current.game_engine_subroutine == v8.base.FLAGPOLE_SLIDE
            )

            events = derive_game_events(previous, current)
            if any(event.kind == GameEventType.LEVEL_COMPLETED for event in events):
                terminal = CandidateTerminal.LEVEL_COMPLETE
            elif any(event.kind == GameEventType.DIED for event in events):
                terminal = CandidateTerminal.DEATH
            elif _candidate_pit_risk(current):
                terminal = CandidateTerminal.DEATH

            previous = current
            if terminal != CandidateTerminal.NONE:
                break
        if terminal != CandidateTerminal.NONE:
            break

    v8.base.set_nes_controller_state(core, 0, 0x00)
    return CandidateOutcome(
        candidate=candidate,
        start_x=start_observation.mario_x_abs,
        end_x=previous.mario_x_abs,
        max_x=max_x,
        elapsed_frames=elapsed,
        terminal=terminal,
        reached_flagpole=reached_flagpole,
    )


def strict_drain_to_authoritative_death(core, current, episode, timeout_s):
    """Stop planning on a doomed fall but never fabricate an authoritative death."""
    print(
        f"V10 fall guard: unrecoverable pit fall at frame={current.native_frame_id} "
        f"X={current.mario_x_abs} Y={current.mario_y} "
        f"VY={_signed_byte(current.player_y_speed):+d}; planning stopped",
        flush=True,
    )
    v8.base.set_nes_controller_state(core, 0, 0x00)
    previous = current

    for drain_index in range(1, v9.PIT_DRAIN_MAX_FRAMES + 1):
        v8.base.step(core, timeout_s)
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)
        events = derive_game_events(previous, current)
        episode.record(current, events)
        died = any(event.kind == GameEventType.DIED for event in events)

        if v8._web_viewer is not None and (
            drain_index % max(1, v8._web_sample_stride) == 0 or died
        ):
            v8._web_viewer.publish_core(
                core,
                current,
                decision=v8._commit_decision,
                mode="PIT-FALL TERMINAL",
                action="controls released",
            )

        if died:
            print(
                f"V10 fall guard: authoritative DIED edge confirmed "
                f"frame={current.native_frame_id} "
                f"engine=0x{current.game_engine_subroutine:02X}",
                flush=True,
            )
            return current, CandidateTerminal.DEATH
        previous = current

    print(
        f"{_V10_RESULT_PREFIX} FAIL unrecoverable pit fall without authoritative "
        f"DIED edge after {v9.PIT_DRAIN_MAX_FRAMES} frames | "
        f"frame={previous.native_frame_id} X={previous.mario_x_abs}",
        flush=True,
    )
    # The supervisor terminates this worker as soon as it sees the line above.
    # Do not return CandidateTerminal.DEATH: that would falsely claim a DIED edge.
    threading.Event().wait()
    raise AssertionError("unreachable")


def main() -> int:
    # v6.evaluate_candidates() and v6.beam_search() resolve this module-global
    # symbol dynamically, so one patch covers coarse, precision, recovery and
    # deeper beam rollouts without changing authoritative commit execution.
    v6.run_candidate = run_candidate_pit_aware
    v9._drain_to_authoritative_death = strict_drain_to_authoritative_death
    print(
        "Planner V10: gap-aware candidate rejection enabled "
        f"(pit-risk Y>={PLANNING_PIT_RISK_Y} while descending)",
        flush=True,
    )
    return v9.main()


def _result_code(line: str) -> int | None:
    if line.startswith(_V10_RESULT_PREFIX):
        return 8
    if line.startswith(_RESULT_PREFIX):
        if "PASS" in line:
            return 0
        if "FAIL death" in line:
            return 6
        if "FAIL decision limit" in line:
            return 7
        if "FAIL" in line:
            return 1
    if "MesenLoadError:" in line and "FamiPixelStepFrame failed" in line:
        return 4
    return None


def _reader(stream, out_queue: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stream.readline, ""):
            out_queue.put(line)
    finally:
        out_queue.put(None)


def _terminate_worker(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2.0)


def _compact(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    prefixes = (
        "Planner V10:",
        "Planner V9:",
        "Web UI",
        "Planner V8:",
        "V8 fast search:",
        "DLL",
        "ROM",
        "Init",
        "NesConfig",
        "LoadRom",
        "Debugger",
        "TitleMenu",
        "GameEntry",
        "Decision #",
        "CONTROL ALERT",
        "V8 fast gate:",
        "V8 hazard:",
        "V8 hazard search:",
        "V8 branch governor:",
        "V9 recovery probe:",
        "V9 stuck recovery:",
        "V10 fall guard:",
        "Beam choice:",
        "Selected action:",
        "Decision mode",
        "Committed state:",
        "=== LEVEL COMPLETE",
        "PlannerV7:",
        "PlannerV10:",
        "V8 beam error:",
        "[CPU]",
    )
    return s.startswith(prefixes)


def _supervise() -> int:
    env = os.environ.copy()
    env[_WORKER_ENV] = "1"
    verbose = "--verbose-log" in sys.argv[1:]
    cmd = [sys.executable, "-u", str(__file__), *sys.argv[1:]]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None

    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True).start()
    last_output = time.monotonic()
    traceback_mode = False

    while True:
        try:
            item = lines.get(timeout=0.25)
        except queue.Empty:
            if proc.poll() is not None:
                return proc.returncode or 0
            if time.monotonic() - last_output > _NO_OUTPUT_TIMEOUT_S:
                print(
                    f"[{_timestamp()}] V10 supervisor: FAIL no worker output for "
                    f"{_NO_OUTPUT_TIMEOUT_S:.0f}s; terminating stalled worker.",
                    flush=True,
                )
                _terminate_worker(proc)
                return 124
            continue

        if item is None:
            return proc.wait()

        last_output = time.monotonic()
        stripped = item.strip()
        if stripped.startswith("Traceback (most recent call last):"):
            traceback_mode = True
        if verbose or traceback_mode or _compact(item):
            if stripped:
                print(f"[{_timestamp()}] {item}", end="", flush=True)

        code = _result_code(stripped)
        if code is None:
            continue

        if code in (4, 8):
            _terminate_worker(proc)
            return code

        try:
            proc.wait(timeout=_POST_RESULT_GRACE_S)
        except subprocess.TimeoutExpired:
            print(
                f"[{_timestamp()}] V10 supervisor: terminal planner result observed; "
                "terminating completed native worker.",
                flush=True,
            )
            _terminate_worker(proc)
        return code


def _cli() -> None:
    if os.environ.get(_WORKER_ENV) == "1":
        code = main()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    raise SystemExit(_supervise())


if __name__ == "__main__":
    _cli()
