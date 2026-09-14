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
"""

from __future__ import annotations

import os
import sys

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
    """Choose a bounded semantic/state-diverse subset from V7 root probes.

    Four forward-control families are retained when available because they map
    directly to the dimensions V7 was created to expose: run+jump, run, jump,
    and ordinary forward motion. Remaining slots are filled by the strongest
    candidates that add a previously unseen dynamic-state signature. Any final
    empty slots fall back to overall rank.
    """
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
    # V7 resolves these functions through the shared v6 module object at runtime,
    # so the patch is local to this process and leaves the reusable V6/V7 source
    # contracts untouched.
    v6.evaluate_candidates = evaluate_candidates
    v6.beam_search = beam_search
    print("=== Planner V8: bounded B-aware precision branching ===", flush=True)
    print(
        f"Precision branch governor: immediate probes stay at {len(v7.PRECISION_CANDIDATES)}, "
        f"deep beam shortlist <= {PRECISION_SHORTLIST_LIMIT}",
        flush=True,
    )
    return v7.main()


def _cli() -> None:
    """Run the planner and return control to the shell even if Mesen teardown stalls.

    The planner's result is fully determined before this point. Mesen's native
    runtime can keep Python alive during interpreter/DLL teardown after a clean
    result, so the CLI deliberately bypasses process finalizers once stdout and
    stderr have been flushed. This does not change planner execution or search
    semantics; it only bounds post-result shutdown.
    """
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    _cli()
