#!/usr/bin/env python3
"""V8 branch-governed wrapper around the V7 B-aware planner.

V7 proved that the richer B-aware control space is useful, but its precision
beam expands all 27 short primitives at every node. That turns one precision
trigger into hundreds of Mesen counterfactual rollouts and repeated save/load
operations, which is exactly where the latest machine run hit native frame-step
status 4.

V8 keeps the V7 control model and immediate precision probes unchanged, then
reduces only the *beam branching factor*. It reuses those already-evaluated root
results to select a small semantic/state-diverse shortlist before deeper search.
Mesen remains machine authority; only the chosen root action is committed.

The CLI uses a small supervisor process. Mesen's native runtime can remain alive
after the planner has already printed a terminal PASS/FAIL result, before the
Python frame returns to the caller. Therefore an in-process os._exit() is too
late. The parent process watches the worker output, recognizes the authoritative
planner result, and terminates only the already-finished native worker if it does
not exit promptly.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time

from fami_pixel.games.smb1 import CandidateTerminal, score_candidate

import mesen_smb_checkpoint_planner_v6 as v6
import mesen_smb_checkpoint_planner_v7 as v7


PRECISION_SHORTLIST_LIMIT = 6
MANDATORY_FORWARD_FAMILIES = (
    "right_a_b",  # running jump
    "right_b",    # running / speed build
    "right_a",    # ordinary jump
    "right",      # ordinary forward motion
)

_WORKER_ENV = "FAMI_PIXEL_V8_WORKER"
_RESULT_PREFIX = "PlannerV7:"
_NO_OUTPUT_TIMEOUT_S = 180.0
_POST_RESULT_GRACE_S = 1.0

_original_evaluate_candidates = v6.evaluate_candidates
_original_beam_search = v6.beam_search
_last_precision_results = None


def _family(name: str) -> str:
    """Return the semantic action family without its frame-duration suffix."""
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


def beam_search(core, candidates, root_file, root_frame, root_x, root_engine,
                root_observation, timeout_s, depth, width, tag):
    if tag != "v7-precision" or _last_precision_results is None:
        return _original_beam_search(
            core, candidates, root_file, root_frame, root_x, root_engine,
            root_observation, timeout_s, depth, width, tag,
        )

    shortlist_results = precision_shortlist(_last_precision_results)
    shortlist = tuple(e.outcome.candidate for e in shortlist_results)
    shortlist_width = min(width, len(shortlist))
    root_name, logs = _original_beam_search(
        core, shortlist, root_file, root_frame, root_x, root_engine,
        root_observation, timeout_s, depth, shortlist_width, "v8-precision",
    )
    names = ", ".join(e.outcome.candidate.name for e in shortlist_results)
    prefix = (
        f"  V8 branch governor: {len(candidates)} -> {len(shortlist)} precision families/states",
        f"  V8 shortlist: {names}",
    )
    return root_name, prefix + logs


def main() -> int:
    v6.evaluate_candidates = evaluate_candidates
    v6.beam_search = beam_search
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
                    f"V8 supervisor: FAIL no worker output for {_NO_OUTPUT_TIMEOUT_S:.0f}s; terminating stalled worker.",
                    flush=True,
                )
                _terminate_worker(proc)
                return 124
            continue

        if item is None:
            return proc.wait()

        last_output = time.monotonic()
        print(item, end="", flush=True)
        code = _result_code(item.strip())
        if code is None:
            continue

        try:
            proc.wait(timeout=_POST_RESULT_GRACE_S)
        except subprocess.TimeoutExpired:
            print(
                "V8 supervisor: terminal planner result observed; terminating completed native worker.",
                flush=True,
            )
            _terminate_worker(proc)
        return code


def _cli() -> None:
    if os.environ.get(_WORKER_ENV) == "1":
        # If the worker returns naturally, bypass interpreter/DLL finalization.
        # If it stalls before returning, the parent supervisor will terminate it
        # after observing the terminal planner result line.
        code = main()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)

    raise SystemExit(_supervise())


if __name__ == "__main__":
    _cli()
