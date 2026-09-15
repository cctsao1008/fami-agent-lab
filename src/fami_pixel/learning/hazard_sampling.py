"""Hazard-focused branch selection for offline SMB1 teacher collection.

The collector still uses Mesen as the only transition oracle. These helpers
only choose which non-terminal counterfactual outcomes should become future
root states or receive extended neutral death probes.
"""

from __future__ import annotations

from typing import Iterable


def _is_non_terminal(outcome) -> bool:
    terminal = getattr(outcome, "terminal", None)
    value = getattr(terminal, "value", terminal)
    return value == "none"


def select_hazard_branches(outcomes: Iterable, width: int = 3) -> tuple:
    """Pick diverse surviving outcomes for breadth-first teacher expansion."""
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


def select_death_probe_candidates(outcomes: Iterable, width: int = 6) -> tuple:
    """Pick surviving outcomes worth extending with neutral Mesen rollout.

    The probe set deliberately covers both obviously weak actions and forward
    actions that may have crossed a delayed-death boundary. Immediate terminal
    outcomes do not need probing because they already carry authoritative
    terminal labels.
    """
    width = int(width)
    if width <= 0:
        raise ValueError("width must be > 0")

    safe = [outcome for outcome in outcomes if _is_non_terminal(outcome)]
    if not safe:
        return ()

    ordered = sorted(
        safe,
        key=lambda o: (int(o.progress), int(o.max_x), str(o.candidate.name)),
    )
    low = ordered[0]
    boundary = min(
        safe,
        key=lambda o: (abs(int(o.progress)), int(o.progress), int(o.max_x), str(o.candidate.name)),
    )
    leader = max(
        safe,
        key=lambda o: (int(o.max_x), int(o.progress), str(o.candidate.name)),
    )

    chosen = []
    seen = set()

    def add(outcome) -> None:
        name = str(outcome.candidate.name)
        if name not in seen:
            chosen.append(outcome)
            seen.add(name)

    for outcome in (low, boundary, leader):
        add(outcome)
        if len(chosen) >= width:
            return tuple(chosen)

    # Fill from both ends so delayed-death probes do not collapse to only
    # stalled actions or only forward actions.
    left = 0
    right = len(ordered) - 1
    while len(chosen) < width and left <= right:
        add(ordered[left])
        left += 1
        if len(chosen) >= width or left > right:
            break
        add(ordered[right])
        right -= 1

    return tuple(chosen)


def _entry_state_distance(left, right) -> tuple[int, int]:
    a = tuple(int(v) for v in left.state_signature)
    b = tuple(int(v) for v in right.state_signature)
    differing = sum(x != y for x, y in zip(a, b))
    magnitude = sum(abs(x - y) for x, y in zip(a, b))
    return differing, magnitude


def select_depth_beam(entries: Iterable, width: int = 3, x_window: int = 96) -> tuple:
    """Prune one expanded layer to a small forward-moving hazard beam."""
    width = int(width)
    x_window = int(x_window)
    if width <= 0:
        raise ValueError("width must be > 0")
    if x_window < 0:
        raise ValueError("x_window must be >= 0")

    pool = list(entries)
    if not pool:
        return ()

    leader = max(
        pool,
        key=lambda e: (
            int(e.child_x),
            int(e.outcome.max_x),
            int(e.outcome.progress),
            str(e.outcome.candidate.name),
        ),
    )
    frontier_min_x = int(leader.child_x) - x_window
    near = [entry for entry in pool if int(entry.child_x) >= frontier_min_x]

    boundary = min(
        near,
        key=lambda e: (
            abs(int(e.outcome.progress)),
            -int(e.child_x),
            -int(e.outcome.max_x),
            str(e.outcome.candidate.name),
        ),
    )

    chosen = []
    chosen_ids = set()

    def add(entry) -> None:
        identity = id(entry)
        if identity not in chosen_ids:
            chosen.append(entry)
            chosen_ids.add(identity)

    add(leader)
    if len(chosen) >= width:
        return tuple(chosen)
    add(boundary)
    if len(chosen) >= width:
        return tuple(chosen)

    remaining = [entry for entry in near if id(entry) not in chosen_ids]
    if remaining:
        diverse = max(
            remaining,
            key=lambda entry: (
                min(_entry_state_distance(entry, picked) for picked in chosen),
                int(entry.child_x),
                int(entry.outcome.max_x),
            ),
        )
        add(diverse)
        if len(chosen) >= width:
            return tuple(chosen)

    for entry in sorted(
        pool,
        key=lambda e: (
            int(e.child_x),
            int(e.outcome.max_x),
            int(e.outcome.progress),
        ),
        reverse=True,
    ):
        add(entry)
        if len(chosen) >= width:
            break

    return tuple(chosen)
