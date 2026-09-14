#!/usr/bin/env python3
"""V8 branch-governed wrapper around the V7 B-aware planner.

V7 proved that the richer B-aware control space is useful, but its precision
beam expands all 27 short primitives at every node. V8 keeps all immediate
probes, then limits only deeper beam branching.

Optional ``--web-ui`` observability is strictly authoritative: candidate/beam
rollouts remain hidden, while only the selected action replay publishes sampled
NES frames and telemetry to a localhost browser viewer. Sampling never sleeps or
paces the authoritative emulator loop.
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


PRECISION_SHORTLIST_LIMIT = 6
MANDATORY_FORWARD_FAMILIES = (
    "right_a_b",
    "right_b",
    "right_a",
    "right",
)

_WORKER_ENV = "FAMI_PIXEL_V8_WORKER"
_RESULT_PREFIX = "PlannerV7:"
_NO_OUTPUT_TIMEOUT_S = 180.0
_POST_RESULT_GRACE_S = 1.0
_NES_NOMINAL_FPS = 60.0

_original_evaluate_candidates = v6.evaluate_candidates
_original_beam_search = v6.beam_search
_original_select_immediate = v6.select_immediate
_last_precision_results = None
_last_selection_mode = "B-AWARE COARSE FAST"
_web_viewer: NesWebViewer | None = None
_web_fps = 15.0
_web_sample_stride = 4
_commit_decision = 0
_commit_frame_index = 0


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


def precision_shortlist(results, limit: int = PRECISION_SHORTLIST_LIMIT):
    """Choose a bounded semantic/state-diverse subset from V7 root probes."""
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
        if e.outcome.candidate.name in selected_names:
            continue
        if e.signature not in seen_signatures:
            add(e)

    for e in ranked:
        if len(selected) >= limit:
            break
        add(e)

    return tuple(selected)


def evaluate_candidates(*args, **kwargs):
    global _last_precision_results
    results = _original_evaluate_candidates(*args, **kwargs)
    if kwargs.get("mode") == "precision":
        _last_precision_results = results
    return results


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

    shortlist_results = precision_shortlist(_last_precision_results)
    shortlist = tuple(e.outcome.candidate for e in shortlist_results)
    shortlist_width = min(width, len(shortlist))
    try:
        root_name, logs = _original_beam_search(
            core, shortlist, root_file, root_frame, root_x, root_engine,
            root_observation, timeout_s, depth, shortlist_width, "v8-precision",
        )
    except Exception as exc:
        names = ", ".join(e.outcome.candidate.name for e in shortlist_results)
        print(
            f"V8 beam error: tag=v8-precision root_frame={root_frame} X={root_x} "
            f"depth={depth} width={shortlist_width} shortlist=[{names}] "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )
        raise

    _last_selection_mode = "B-AWARE PRECISION"
    names = ", ".join(e.outcome.candidate.name for e in shortlist_results)
    prefix = (
        f"  V8 branch governor: {len(candidates)} -> {len(shortlist)} precision families/states",
        f"  V8 shortlist: {names}",
    )
    return root_name, prefix + logs


def _observed_commit_candidate(core, candidate, start_observation, episode, timeout_s):
    """Replay the selected candidate and sample authoritative frames without pacing it."""
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

            level_complete = any(
                event.kind == GameEventType.LEVEL_COMPLETED for event in events
            )
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


def _consume_web_args() -> tuple[bool, int, float]:
    """Remove V8-only web flags before V7 argparse sees argv."""
    enabled = False
    port = 8765
    fps = 15.0
    cleaned = [sys.argv[0]]
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--web-ui":
            enabled = True
            i += 1
            continue
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
    sys.argv[:] = cleaned
    return enabled, port, fps


def main() -> int:
    global _web_viewer, _web_fps, _web_sample_stride

    web_enabled, web_port, _web_fps = _consume_web_args()
    _web_sample_stride = max(1, round(_NES_NOMINAL_FPS / _web_fps))
    v6.evaluate_candidates = evaluate_candidates
    v6.beam_search = beam_search
    v6.select_immediate = select_immediate

    if web_enabled:
        _web_viewer = NesWebViewer(port=web_port)
        _web_viewer.start()
        v7.commit_candidate = _observed_commit_candidate
        print(f"Web UI    : {_web_viewer.url}", flush=True)
        print(
            "Web scope : authoritative commits only; beam rollouts hidden; "
            f"sampling~{_NES_NOMINAL_FPS / _web_sample_stride:.1f} fps "
            f"(every {_web_sample_stride} committed frames, no playback sleep)",
            flush=True,
        )
        try:
            webbrowser.open(_web_viewer.url, new=2)
        except Exception:
            pass

    print("=== Planner V8: bounded B-aware precision branching ===", flush=True)
    print(
        f"Precision branch governor: immediate probes stay at {len(v7.PRECISION_CANDIDATES)}, "
        f"deep beam shortlist <= {PRECISION_SHORTLIST_LIMIT}",
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
    else:
        print(line, end="", flush=True)


def _supervise() -> int:
    env = os.environ.copy()
    env[_WORKER_ENV] = "1"
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
        _print_timestamped(item)
        code = _result_code(item.strip())
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
