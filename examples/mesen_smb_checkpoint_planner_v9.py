#!/usr/bin/env python3
"""V9 obstacle-recovery wrapper around the fast observable V8 planner.

V8 is efficient on open terrain but can get trapped immediately against a tall
pipe: all short precision primitives report zero forward progress, so the
planner keeps committing tiny RIGHT+A+B actions without creating enough jump
height or run-up distance. V9 keeps the V8 fast path and probes a tiny set of
long-horizon recovery macros first when V8 reports ``no-forward-progress``.

V9 also guards authoritative execution against a visibly unrecoverable pit fall.
The guard does not redefine the durable DIED event: once Mario is below the
playable floor envelope and still descending, planning stops and controls are
released while Mesen advances until the source-audited DIED engine edge appears.

Mesen remains the machine authority. Recovery candidates are counterfactual
save-state rollouts; only the selected root candidate is committed.
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
    ActionCommand,
    CandidateTerminal,
    GameEventType,
    PlanCandidate,
    Smb1Action,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)

import mesen_smb_checkpoint_planner_v6 as v6
import mesen_smb_checkpoint_planner_v7 as v7
import mesen_smb_checkpoint_planner_v8 as v8


RECOVERY_CANDIDATES = (
    PlanCandidate(
        "stuck_long_jump",
        (
            ActionCommand(Smb1Action.RIGHT_A_B, 24),
            ActionCommand(Smb1Action.RIGHT_B, 12),
        ),
    ),
    PlanCandidate(
        "stuck_max_jump",
        (
            ActionCommand(Smb1Action.RIGHT_A_B, 32),
            ActionCommand(Smb1Action.RIGHT_B, 12),
        ),
    ),
    PlanCandidate(
        "stuck_backoff_run_jump",
        (
            ActionCommand(Smb1Action.LEFT, 12),
            ActionCommand(Smb1Action.RIGHT_B, 8),
            ActionCommand(Smb1Action.RIGHT_A_B, 24),
            ActionCommand(Smb1Action.RIGHT_B, 12),
        ),
    ),
)

RECOVERY_NAMES = frozenset(c.name for c in RECOVERY_CANDIDATES)
PIT_FALL_Y = 208
PIT_DRAIN_MAX_FRAMES = 120
_WORKER_ENV = "FAMI_PIXEL_V9_WORKER"
_RESULT_PREFIX = "PlannerV7:"
_NO_OUTPUT_TIMEOUT_S = 180.0
_POST_RESULT_GRACE_S = 1.0

_original_v8_evaluate = v8.evaluate_candidates
_original_v8_beam = v8.beam_search
_original_v8_observed_commit = v8._observed_commit_candidate
_original_v7_commit = v7.commit_candidate


for _candidate, _label in zip(
    RECOVERY_CANDIDATES,
    (
        "RECOVERY RIGHT+A+B 24f -> RIGHT+B 12f",
        "RECOVERY RIGHT+A+B 32f -> RIGHT+B 12f",
        "RECOVERY LEFT 12f -> RIGHT+B 8f -> RIGHT+A+B 24f -> RIGHT+B 12f",
    ),
):
    v7.ACTION_LABELS[_candidate.name] = _label
    v6.ACTION_LABELS[_candidate.name] = _label


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def _is_unrecoverable_pit_fall(observation) -> bool:
    return (
        observation.game_engine_subroutine == v8.base.PLAYER_CONTROL
        and observation.mario_y >= PIT_FALL_Y
        and _signed_byte(observation.player_y_speed) > 0
    )


def _drain_to_authoritative_death(core, current, episode, timeout_s):
    """Stop planning and let Mesen reach its real DIED edge after a doomed fall."""
    print(
        f"V9 fall guard: unrecoverable pit fall at frame={current.native_frame_id} "
        f"X={current.mario_x_abs} Y={current.mario_y} "
        f"VY={_signed_byte(current.player_y_speed):+d}; planning stopped",
        flush=True,
    )
    v8.base.set_nes_controller_state(core, 0, 0x00)
    previous = current

    for drain_index in range(1, PIT_DRAIN_MAX_FRAMES + 1):
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
                f"V9 fall guard: authoritative DIED edge confirmed "
                f"frame={current.native_frame_id} engine=0x{current.game_engine_subroutine:02X}",
                flush=True,
            )
            return current, CandidateTerminal.DEATH
        previous = current

    print(
        f"V9 fall guard: no DIED edge within {PIT_DRAIN_MAX_FRAMES} frames; "
        "terminating doomed trajectory without further planning",
        flush=True,
    )
    return previous, CandidateTerminal.DEATH


def _guard_commit_result(core, result, episode, timeout_s):
    current, terminal, reached_flagpole = result
    if terminal == CandidateTerminal.NONE and _is_unrecoverable_pit_fall(current):
        current, terminal = _drain_to_authoritative_death(
            core, current, episode, timeout_s
        )
    return current, terminal, reached_flagpole


def _guarded_observed_commit(core, candidate, start_observation, episode, timeout_s):
    result = _original_v8_observed_commit(
        core, candidate, start_observation, episode, timeout_s
    )
    return _guard_commit_result(core, result, episode, timeout_s)


def _guarded_headless_commit(core, candidate, start_observation, episode, timeout_s):
    result = _original_v7_commit(
        core, candidate, start_observation, episode, timeout_s
    )
    return _guard_commit_result(core, result, episode, timeout_s)


def _evaluate_only(args, kwargs, candidates):
    local_args = list(args)
    local_args[1] = tuple(candidates)
    return _original_v8_evaluate(*local_args, **kwargs)


def evaluate_candidates(*args, **kwargs):
    """Probe only the three recovery macros first on a no-progress state."""
    mode = kwargs.get("mode")
    if (
        not v8._full_search
        and mode == "precision"
        and v8._last_precision_reason == "no-forward-progress"
    ):
        recovery = _evaluate_only(args, kwargs, RECOVERY_CANDIDATES)
        v8._last_precision_results = recovery
        safe_recovery = [
            e for e in recovery
            if e.outcome.terminal == CandidateTerminal.NONE and e.outcome.progress > 0
        ]
        if safe_recovery:
            best = max(safe_recovery, key=v8._rank)
            print(
                "V9 recovery probe: "
                f"best={v7.action_label(best.outcome.candidate.name)} "
                f"progress={best.outcome.progress:+d}; short precision skipped",
                flush=True,
            )
            return recovery

        print(
            "V9 recovery probe: no safe forward macro; falling back to short precision",
            flush=True,
        )
        precision = _original_v8_evaluate(*args, **kwargs)
        v8._last_precision_results = precision
        return precision

    return _original_v8_evaluate(*args, **kwargs)


def beam_search(core, candidates, root_file, root_frame, root_x, root_engine,
                root_observation, timeout_s, depth, width, tag):
    """Select a proven recovery macro without another beam expansion."""
    if (
        not v8._full_search
        and tag == "v7-precision"
        and v8._last_precision_reason == "no-forward-progress"
        and v8._last_precision_results is not None
    ):
        safe_recovery = [
            e for e in v8._last_precision_results
            if e.outcome.candidate.name in RECOVERY_NAMES
            and e.outcome.terminal == CandidateTerminal.NONE
            and e.outcome.progress > 0
        ]
        if safe_recovery:
            chosen = max(safe_recovery, key=v8._rank)
            v8._last_selection_mode = "B-AWARE STUCK RECOVERY"
            return chosen.outcome.candidate.name, (
                "  V9 stuck recovery: recovery macro proved forward progress; beam skipped",
                f"  Beam choice: {v7.action_label(chosen.outcome.candidate.name)} | "
                f"projected progress={chosen.outcome.progress:+d} | status=SAFE",
            )

    return _original_v8_beam(
        core, candidates, root_file, root_frame, root_x, root_engine,
        root_observation, timeout_s, depth, width, tag,
    )


def main() -> int:
    v8.evaluate_candidates = evaluate_candidates
    v8.beam_search = beam_search
    v8._observed_commit_candidate = _guarded_observed_commit
    v7.commit_candidate = _guarded_headless_commit
    print(
        "Planner V9: obstacle recovery + authoritative pit-fall terminal guard enabled",
        flush=True,
    )
    return v8.main()


def _result_code(line: str) -> int | None:
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
        "V9 fall guard:",
        "Beam choice:",
        "Selected action:",
        "Decision mode",
        "Committed state:",
        "=== LEVEL COMPLETE",
        "PlannerV7:",
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
                    f"[{_timestamp()}] V9 supervisor: FAIL no worker output for "
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

        if code == 4:
            print(
                f"[{_timestamp()}] V9 supervisor: native frame-step failure observed; "
                "terminating failed worker immediately.",
                flush=True,
            )
            _terminate_worker(proc)
            return code

        try:
            proc.wait(timeout=_POST_RESULT_GRACE_S)
        except subprocess.TimeoutExpired:
            print(
                f"[{_timestamp()}] V9 supervisor: terminal planner result observed; "
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
