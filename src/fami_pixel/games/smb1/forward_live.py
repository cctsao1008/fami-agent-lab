"""Latency-tolerant helpers for live SMB1 forward-model control.

Offline trajectory evaluation can wait for a complete candidate set. Live
receding-horizon control cannot: one unresolved branch may consume the whole
horizon while a jump branch already proved a safe landing. These helpers keep
that distinction explicit.
"""

from __future__ import annotations

from .actions import action_to_nes_buttons
from .forward_model import execution_prefix_schedule
from .trajectory import TrajectoryPlan


def response_proves_execution_prefix(response: dict, prefix_frames: int) -> bool:
    """Return whether a worker result proves the prefix we intend to execute.

    A resolved event that happens before the live execution prefix ends cannot
    certify the frames after that event. This specifically rejects the V23 field
    failure where a stale already-airborne root reported ``landed`` after 1-2
    simulated frames while the authority intended to execute a 4-frame prefix.
    """

    if prefix_frames <= 0:
        raise ValueError("prefix_frames must be > 0")
    if not bool(response.get("trajectory_safe_resolved", False)):
        return False
    if str(response.get("trajectory_event")) == "win":
        return True
    try:
        return int(response.get("trajectory_frames", 0)) >= int(prefix_frames)
    except (TypeError, ValueError):
        return False


def select_fresh_partial_safe_response(
    responses: list[dict] | tuple[dict, ...],
    *,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    prefix_frames: int,
) -> dict | None:
    """Select the newest safe response without waiting for a complete cohort.

    Worker latency is candidate-dependent. Run/coast/backtrack may hit the hard
    horizon while a jump lands much sooner. Requiring all workers to finish the
    same generation therefore turns the slowest UNKNOWN branch into a global
    control barrier. Live authority instead accepts any individually proven,
    still-fresh prefix and prefers recency before score.
    """

    eligible: list[dict] = []
    for raw in responses:
        if not raw or "error" in raw:
            continue
        try:
            generation = int(raw.get("generation", -1))
            root_frame = int(raw.get("root_frame", -1))
        except (TypeError, ValueError):
            continue
        age = int(current_frame) - root_frame
        if generation <= int(last_applied_generation):
            continue
        if age < 0 or age > int(freshness):
            continue
        if not response_proves_execution_prefix(raw, prefix_frames):
            continue
        eligible.append(dict(raw))

    if not eligible:
        return None

    newest_key = max(
        (int(item["root_frame"]), int(item["generation"]))
        for item in eligible
    )
    newest = [
        item
        for item in eligible
        if (int(item["root_frame"]), int(item["generation"])) == newest_key
    ]
    return max(newest, key=lambda item: tuple(item.get("score") or ()))


def execution_prefix_with_continuation(
    plan: TrajectoryPlan,
    frame_budget: int,
) -> list[dict[str, int]]:
    """Execute a short prefix, then degrade to the plan's declared tail action.

    V23 appended RELEASE. If no fresh cohort arrived, V11 held that final RELEASE
    forever; Mario stopped at X=180 until the watchdog fired. The continuation
    frame makes the held fallback explicit. Jump plans declare RIGHT+B as their
    tail, which releases A for re-arming while preserving forward motion.
    """

    schedule = execution_prefix_schedule(
        plan,
        frame_budget,
        append_release_tail=False,
    )
    schedule.append(
        {
            "buttons": int(action_to_nes_buttons(plan.tail_action)),
            "frames": 1,
        }
    )
    return schedule
