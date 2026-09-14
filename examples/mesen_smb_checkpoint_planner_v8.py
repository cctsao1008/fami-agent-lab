#!/usr/bin/env python3
"""Fast, observable V8 wrapper around the V7 B-aware SMB1 planner.

FAST mode is deliberately adaptive:
- normal coarse search starts with four representative 30-frame actions,
- the remaining coarse actions are evaluated only when progress looks weak,
- scheduled V7 coarse audit beams are disabled unless explicitly requested,
- ordinary precision uses a 4-way/depth-2 budget,
- imminent falling/all-terminal hazards get a larger 6-way/depth-3 budget.

``--full-search`` restores the V7/V8 research-heavy behavior. ``--web-ui`` is
observer-only: only authoritative committed frames are sampled; counterfactual
rollouts remain headless and never pace emulator execution.
"""

from __future__ import annotations

from datetime import datetime
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser

from fami_pixel.games.smb1 import (
    CandidateTerminal,
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
    score_candidate,
)
from fami_pixel.telemetry import NesWebViewer

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v6 as v6
import mesen_smb_checkpoint_planner_v7 as v7


FAST_COARSE_NAMES = frozenset(("cruise", "run", "tap_jump", "run_tap_jump"))
FAST_PROGRESS_THRESHOLD = 60
FAST_PRECISION_SHORTLIST = 4
FAST_PRECISION_DEPTH = 2
FAST_PRECISION_WIDTH = 4
HAZARD_PRECISION_SHORTLIST = 6
HAZARD_PRECISION_DEPTH = 3
HAZARD_PRECISION_WIDTH = 6
LOW_FALL_Y = 150
CAPTURE_AUDIT_INTERVAL = 10
MANDATORY_FORWARD_FAMILIES = (
    "right_a_b",
    "right_b",
    "right_a",
    "right",
)
HAZARD_REASONS = frozenset(("all-coarse-actions-terminal", "coarse-best-descending-low"))

_WORKER_ENV = "FAMI_PIXEL_V8_WORKER"
_RESULT_PREFIX = "PlannerV7:"
_NO_OUTPUT_TIMEOUT_S = 180.0
_POST_RESULT_GRACE_S = 1.0
_NES_NOMINAL_FPS = 60.0

_original_evaluate_candidates = v6.evaluate_candidates
_original_beam_search = v6.beam_search
_original_select_immediate = v6.select_immediate
_original_precision_trigger = v6.precision_trigger
_original_capture_authoritative = v6.capture_authoritative

_last_coarse_results = None
_last_precision_results = None
_last_precision_reason: str | None = None
_last_selection_mode = "B-AWARE COARSE FAST"
_web_viewer: NesWebViewer | None = None
_web_fps = 15.0
_web_sample_stride = 4
_commit_decision = 0
_commit_frame_index = 0
_full_search = False


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _family(name: str) -> str:
    stem, sep, suffix = name.rpartition("_")
    if sep and suffix.isdigit():
        return stem
    return name


def _rank(e) -> tuple[float, int, int, int]:
    terminal_bonus = 2 if e.outcome.terminal == CandidateTerminal.LEVEL_COMPLETE else 0
    flag_bonus = 1 if e.outcome.reached_flagpole else 0
    return (
        score_candidate(e.outcome),
        terminal_bonus + flag_bonus,
        e.outcome.progress,
        e.outcome.max_x,
    )


def _signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def precision_shortlist(results, limit: int):
    """Choose a bounded semantic/state-diverse subset from immediate probes."""
    if limit <= 0:
        raise ValueError("precision shortlist limit must be positive")

    ranked = sorted(results, key=_rank, reverse=True)
    selected = []
    selected_names: set[str] = set()
    seen_signatures: set[tuple[int, ...]] = set()

    def add(e) -> None:
        name = e.outcome.candidate.name
        if name in selected_names or len(selected) >= limit:
            return
        selected.append(e)
        selected_names.add(name)
        seen_signatures.add(e.signature)

    for family in MANDATORY_FORWARD_FAMILIES:
        members = [e for e in ranked if _family(e.outcome.candidate.name) == family]
        if members:
            add(members[0])

    for e in ranked:
        if len(selected) >= limit:
            break
        if e.outcome.candidate.name not in selected_names and e.signature not in seen_signatures:
            add(e)

    for e in ranked:
        if len(selected) >= limit:
            break
        add(e)

    return tuple(selected)


def _call_evaluate(args, kwargs, candidates):
    local_args = list(args)
    local_args[1] = tuple(candidates)
    return _original_evaluate_candidates(*local_args, **kwargs)


def evaluate_candidates(*args, **kwargs):
    """Use two-stage coarse evaluation and no endpoint captures in FAST mode."""
    global _last_coarse_results, _last_precision_results

    mode = kwargs.get("mode")
    if not _full_search:
        kwargs["capture_dir"] = None

    if not _full_search and mode == "coarse":
        all_candidates = tuple(args[1])
        fast_candidates = tuple(c for c in all_candidates if c.name in FAST_COARSE_NAMES)
        fast_results = _call_evaluate(args, kwargs, fast_candidates)
        safe = [e for e in fast_results if e.outcome.terminal == CandidateTerminal.NONE]
        best_progress = max((e.outcome.progress for e in safe), default=-10**9)

        if safe and best_progress >= FAST_PROGRESS_THRESHOLD:
            results = fast_results
        else:
            remaining = tuple(c for c in all_candidates if c.name not in FAST_COARSE_NAMES)
            extra_results = _call_evaluate(args, kwargs, remaining) if remaining else ()
            by_name = {
                e.outcome.candidate.name: e
                for e in (*fast_results, *extra_results)
            }
            results = tuple(by_name[c.name] for c in all_candidates if c.name in by_name)

        _last_coarse_results = results
        return results

    results = _original_evaluate_candidates(*args, **kwargs)
    if mode == "coarse":
        _last_coarse_results = results
    elif mode == "precision":
        _last_precision_results = results
    return results


def capture_authoritative(state_file, capture_dir, decision, frame, x):
    """Keep sparse durable witnesses instead of copying/hashing every decision."""
    if _full_search or decision == 1 or decision % CAPTURE_AUDIT_INTERVAL == 0:
        return _original_capture_authoritative(state_file, capture_dir, decision, frame, x)
    return None


def precision_trigger(results):
    """Escalate only when coarse state predicts real risk; skip healthy ties."""
    global _last_precision_reason

    reason = _original_precision_trigger(results)
    if _full_search:
        _last_precision_reason = reason
        return reason

    safe = [e for e in results if e.outcome.terminal == CandidateTerminal.NONE]
    if not safe:
        _last_precision_reason = reason
        return reason

    best = max(safe, key=_rank)
    obs = best.observation
    descending_low = (
        obs.player_state == 2
        and _signed_byte(obs.player_y_speed) > 0
        and obs.mario_y >= LOW_FALL_Y
    )
    if descending_low:
        reason = "coarse-best-descending-low"
        print(
            f"V8 hazard: coarse best ends descending low "
            f"(Y={obs.mario_y}, VY={_signed_byte(obs.player_y_speed):+d}); precision required",
            flush=True,
        )
        _last_precision_reason = reason
        return reason

    best_progress = max(e.outcome.progress for e in safe)
    if reason == "state-divergent-progress-tie" and best_progress >= FAST_PROGRESS_THRESHOLD:
        print(
            f"V8 fast gate: coarse progress={best_progress:+d}; precision search skipped",
            flush=True,
        )
        _last_precision_reason = None
        return None

    _last_precision_reason = reason
    return reason


def select_immediate(results):
    global _last_selection_mode
    _last_selection_mode = "B-AWARE COARSE FAST"
    return _original_select_immediate(results)


def beam_search(core, candidates, root_file, root_frame, root_x, root_engine,
                root_observation, timeout_s, depth, width, tag):
    global _last_selection_mode

    if tag != "v7-precision" or _last_precision_results is None:
        if tag == "v7-coarse":
            _last_selection_mode = "B-AWARE COARSE BEAM"
        try:
            return _original_beam_search(
                core, candidates, root_file, root_frame, root_x, root_engine,
                root_observation, timeout_s, depth, width, tag,
            )
        except Exception as exc:
            print(
                f"V8 beam error: tag={tag} root_frame={root_frame} X={root_x} "
                f"depth={depth} width={width} error={type(exc).__name__}: {exc}",
                flush=True,
            )
            raise

    hazard = (not _full_search and _last_precision_reason in HAZARD_REASONS)
    if _full_search:
        limit = len(v7.PRECISION_CANDIDATES)
        effective_depth = depth
        effective_width = width
    elif hazard:
        limit = HAZARD_PRECISION_SHORTLIST
        effective_depth = min(depth, HAZARD_PRECISION_DEPTH)
        effective_width = min(width, HAZARD_PRECISION_WIDTH)
        print(
            f"V8 hazard search: reason={_last_precision_reason}; "
            f"beam <= {limit}x{effective_depth}",
            flush=True,
        )
    else:
        limit = FAST_PRECISION_SHORTLIST
        effective_depth = min(depth, FAST_PRECISION_DEPTH)
        effective_width = min(width, FAST_PRECISION_WIDTH)

    shortlist_results = precision_shortlist(_last_precision_results, limit)
    shortlist = tuple(e.outcome.candidate for e in shortlist_results)
    effective_width = min(effective_width, len(shortlist))

    try:
        root_name, logs = _original_beam_search(
            core, shortlist, root_file, root_frame, root_x, root_engine,
            root_observation, timeout_s, effective_depth, effective_width,
            "v8-precision",
        )
    except Exception as exc:
        names = ", ".join(e.outcome.candidate.name for e in shortlist_results)
        print(
            f"V8 beam error: tag=v8-precision root_frame={root_frame} X={root_x} "
            f"depth={effective_depth} width={effective_width} shortlist=[{names}] "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )
        raise

    _last_selection_mode = "B-AWARE PRECISION"
    prefix = (
        f"  V8 branch governor: {len(candidates)} -> {len(shortlist)}; "
        f"beam depth={effective_depth} width={effective_width}",
    )
    return root_name, prefix + logs


def _observed_commit_candidate(core, candidate, start_observation, episode, timeout_s):
    """Replay selected candidate and sample authoritative frames without pacing it."""
    global _commit_decision, _commit_frame_index
    _commit_decision += 1
    previous = start_observation
    reached_flagpole = previous.game_engine_subroutine == base.FLAGPOLE_SLIDE
    terminal = CandidateTerminal.NONE

    for command in candidate.commands:
        base.set_nes_controller_state(core, 0, command.nes_buttons)
        for _ in range(command.frame_count):
            base.step(core, timeout_s)
            _commit_frame_index += 1
            state = read_smb1_state(core)
            current = observation_from_state(core.frame_count(), state)
            events = derive_game_events(previous, current)
            episode.record(current, events)
            reached_flagpole = reached_flagpole or (
                current.game_engine_subroutine == base.FLAGPOLE_SLIDE
            )

            level_complete = any(event.kind == GameEventType.LEVEL_COMPLETED for event in events)
            died = any(event.kind == GameEventType.DIED for event in events)
            terminal_now = level_complete or died

            if _web_viewer is not None and (
                _commit_frame_index % _web_sample_stride == 0 or terminal_now
            ):
                _web_viewer.publish_core(
                    core,
                    current,
                    decision=_commit_decision,
                    mode=_last_selection_mode,
                    action=v7.action_label(candidate.name),
                )

            if level_complete:
                terminal = CandidateTerminal.LEVEL_COMPLETE
            elif died:
                terminal = CandidateTerminal.DEATH
            previous = current
            if terminal != CandidateTerminal.NONE:
                break
        if terminal != CandidateTerminal.NONE:
            break

    base.set_nes_controller_state(core, 0, 0x00)
    return previous, terminal, reached_flagpole


def _consume_v8_args() -> tuple[bool, int, float, bool]:
    """Remove V8-only flags before V7 argparse sees argv."""
    enabled = False
    port = 8765
    fps = 15.0
    full_search = False
    explicit_audit_interval = False
    cleaned = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--web-ui":
            enabled = True
            i += 1
            continue
        if arg == "--full-search":
            full_search = True
            i += 1
            continue
        if arg == "--verbose-log":
            i += 1
            continue
        if arg == "--audit-interval" or arg.startswith("--audit-interval="):
            explicit_audit_interval = True
        if arg == "--web-port":
            if i + 1 >= len(sys.argv):
                raise ValueError("--web-port requires an integer")
            port = int(sys.argv[i + 1])
            i += 2
            continue
        if arg.startswith("--web-port="):
            port = int(arg.split("=", 1)[1])
            i += 1
            continue
        if arg == "--web-fps":
            if i + 1 >= len(sys.argv):
                raise ValueError("--web-fps requires a number")
            fps = float(sys.argv[i + 1])
            i += 2
            continue
        if arg.startswith("--web-fps="):
            fps = float(arg.split("=", 1)[1])
            i += 1
            continue
        cleaned.append(arg)
        i += 1

    if fps <= 0:
        raise ValueError("--web-fps must be > 0")
    if not full_search and not explicit_audit_interval:
        cleaned.extend(("--audit-interval", "1000000"))
    sys.argv[:] = cleaned
    return enabled, port, fps, full_search


def main() -> int:
    global _web_viewer, _web_fps, _web_sample_stride, _full_search

    web_enabled, web_port, _web_fps, _full_search = _consume_v8_args()
    _web_sample_stride = max(1, round(_NES_NOMINAL_FPS / _web_fps))

    v6.evaluate_candidates = evaluate_candidates
    v6.capture_authoritative = capture_authoritative
    v6.precision_trigger = precision_trigger
    v6.beam_search = beam_search
    v6.select_immediate = select_immediate

    if web_enabled:
        _web_viewer = NesWebViewer(port=web_port)
        _web_viewer.start()
        v7.commit_candidate = _observed_commit_candidate
        print(f"Web UI    : {_web_viewer.url}", flush=True)
        try:
            webbrowser.open(_web_viewer.url, new=2)
        except Exception:
            pass

    profile = "FULL" if _full_search else "FAST"
    print(
        f"Planner V8: profile={profile} web-sample~"
        f"{_NES_NOMINAL_FPS / _web_sample_stride:.1f}fps",
        flush=True,
    )
    if not _full_search:
        print(
            "V8 fast search: adaptive coarse 4->8 on risk; scheduled coarse audit off; "
            f"precision <= {FAST_PRECISION_SHORTLIST}x{FAST_PRECISION_DEPTH}; "
            f"hazard <= {HAZARD_PRECISION_SHORTLIST}x{HAZARD_PRECISION_DEPTH}",
            flush=True,
        )
    return v7.main()


def _result_code(line: str) -> int | None:
    if not line.startswith(_RESULT_PREFIX):
        return None
    if "PASS" in line:
        return 0
    if "FAIL death" in line:
        return 6
    if "FAIL decision limit" in line:
        return 7
    if "FAIL" in line:
        return 1
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


def _print_timestamped(line: str) -> None:
    if line.strip():
        print(f"[{_timestamp()}] {line}", end="", flush=True)


def _compact_log_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    prefixes = (
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
        "FixtureSync",
        "Fixture   :",
        "StateLoad",
        "Decision #",
        "CONTROL ALERT",
        "V8 fast gate:",
        "V8 hazard:",
        "V8 hazard search:",
        "V8 branch governor:",
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
                    f"[{_timestamp()}] V8 supervisor: FAIL no worker output for "
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
        if verbose or traceback_mode or _compact_log_line(item):
            _print_timestamped(item)

        code = _result_code(stripped)
        if code is None:
            continue

        try:
            proc.wait(timeout=_POST_RESULT_GRACE_S)
        except subprocess.TimeoutExpired:
            print(
                f"[{_timestamp()}] V8 supervisor: terminal planner result observed; "
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
