"""Hazard-focused branch selection for offline SMB1 teacher collection.

The collector still uses Mesen as the only transition oracle.  These helpers
only choose which non-terminal counterfactual outcomes should become future
root states so the dataset covers more decision boundaries than a single greedy
trajectory.
"""

from __future__ import annotations

from typing import Iterable


def _is_non_terminal(outcome) -> bool:
    terminal = getattr(outcome, "terminal", None)
    value = getattr(terminal, "value", terminal)
    return value == "none"


def select_hazard_branches(outcomes: Iterable, width: int = 3) -> tuple:
    """Pick diverse surviving outcomes for breadth-first teacher expansion.

    Priority intentionally mixes three views of the same root:
    - lowest progress: seeks stalled/regressive states and nearby hazards;
    - closest-to-zero progress: follows decision-boundary-like transitions;
    - highest progress/max-X: preserves a viable forward trajectory.

    Remaining slots are filled from low to high progress.  Terminal outcomes
    are never promoted to child roots; they remain valuable labelled samples at
    the current root.
    """
    width = int(width)
    if width <= 0:
        raise ValueError("width must be > 0")

    safe = [outcome for outcome in outcomes if _is_non_terminal(outcome)]
    if not safe:
        return ()

    low = min(safe, key=lambda o: (int(o.progress), int(o.max_x), o.candidate.name))
    boundary = min(
        safe,
        key=lambda o: (abs(int(o.progress)), int(o.progress), int(o.max_x), o.candidate.name),
    )
    best = max(safe, key=lambda o: (int(o.progress), int(o.max_x), o.candidate.name))

    chosen = []
    seen = set()
    for outcome in (low, boundary, best):
        name = str(outcome.candidate.name)
        if name not in seen:
            chosen.append(outcome)
            seen.add(name)
        if len(chosen) >= width:
            return tuple(chosen)

    for outcome in sorted(
        safe,
        key=lambda o: (int(o.progress), int(o.max_x), str(o.candidate.name)),
    ):
        name = str(outcome.candidate.name)
        if name in seen:
            continue
        chosen.append(outcome)
        seen.add(name)
        if len(chosen) >= width:
            break

    return tuple(chosen)
