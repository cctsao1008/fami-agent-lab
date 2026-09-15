"""Reusable policy helpers for the SMB1 Mesen forward-model planner.

The forward model itself remains Mesen.  This module only defines the bounded
trajectory vocabulary, short execution-prefix contract, and outcome classes used
by the live receding-horizon integration.
"""

from __future__ import annotations

from .actions import ActionCommand, Smb1Action
from .trajectory import TrajectoryEvent, TrajectoryPlan, TrajectoryResult, trajectory_outcome_key


# Keep this vocabulary deliberately small and auditable.  The live planner may
# expand it later with a beam search, but these are the exact baseline branches
# already validated by the deterministic trajectory probe.
BASELINE_TRAJECTORY_PLANS: tuple[TrajectoryPlan, ...] = (
    TrajectoryPlan(
        "fm_long_jump",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 15),
        ),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "fm_short_jump",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 7),
        ),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "fm_brake_jump",
        (
            ActionCommand(Smb1Action.LEFT_B, 4),
            ActionCommand(Smb1Action.NOOP, 2),
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 15),
        ),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "fm_run",
        (ActionCommand(Smb1Action.RIGHT_B, 8),),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "fm_coast",
        (ActionCommand(Smb1Action.NOOP, 6),),
        tail_action=Smb1Action.NOOP,
    ),
    TrajectoryPlan(
        "fm_backtrack",
        (ActionCommand(Smb1Action.LEFT_B, 8),),
        tail_action=Smb1Action.NOOP,
    ),
)


SAFE_RESOLVED_EVENTS = frozenset(
    {
        TrajectoryEvent.LANDED,
        TrajectoryEvent.CAPABILITY_CHANGED,
        TrajectoryEvent.REWARD_COLLECTED,
        TrajectoryEvent.WIN,
    }
)


def shard_trajectory_plans(
    worker_index: int,
    worker_count: int,
    plans: tuple[TrajectoryPlan, ...] = BASELINE_TRAJECTORY_PLANS,
) -> tuple[TrajectoryPlan, ...]:
    """Deterministically shard the live trajectory vocabulary across workers."""

    if worker_count <= 0:
        raise ValueError("worker_count must be > 0")
    if worker_index < 0 or worker_index >= worker_count:
        raise ValueError("worker_index must be within worker_count")
    return tuple(
        plan
        for index, plan in enumerate(plans)
        if index % worker_count == worker_index
    )


def execution_prefix_schedule(
    plan: TrajectoryPlan,
    frame_budget: int,
    *,
    append_release_tail: bool = True,
) -> list[dict[str, int]]:
    """Return only the first authoritative frames of a planned trajectory.

    The live controller replans after this prefix.  A one-frame RELEASE tail is
    appended by default because V11 holds the last schedule segment when a new
    plan misses its deadline; RELEASE is a conservative fail-safe compared with
    accidentally holding A/B forever.
    """

    if frame_budget <= 0:
        raise ValueError("frame_budget must be > 0")

    remaining = int(frame_budget)
    schedule: list[dict[str, int]] = []
    for command in plan.commands:
        if remaining <= 0:
            break
        frames = min(int(command.frame_count), remaining)
        schedule.append({"buttons": int(command.nes_buttons), "frames": frames})
        remaining -= frames

    if remaining > 0:
        # The explicit prefix ended before the execution budget.  Continue with
        # the plan's declared tail action only for the remaining prefix frames.
        from .actions import action_to_nes_buttons

        schedule.append(
            {
                "buttons": int(action_to_nes_buttons(plan.tail_action)),
                "frames": remaining,
            }
        )

    if append_release_tail:
        schedule.append({"buttons": 0x00, "frames": 1})
    return schedule


def result_is_resolved(result: TrajectoryResult) -> bool:
    return result.event != TrajectoryEvent.HORIZON


def result_is_safe_resolved(result: TrajectoryResult) -> bool:
    return result.event in SAFE_RESOLVED_EVENTS and not result.died


def select_safe_resolved_result(
    results: list[TrajectoryResult] | tuple[TrajectoryResult, ...],
) -> TrajectoryResult | None:
    """Choose only among Mesen-resolved non-death outcomes.

    HORIZON means unknown, not safe.  DEATH is authoritative rejection.  If no
    branch proves a safe resolved outcome, return ``None`` so the caller can use
    its conservative fallback policy instead of laundering uncertainty into a
    positive decision.
    """

    safe = [result for result in results if result_is_safe_resolved(result)]
    if not safe:
        return None
    return max(safe, key=trajectory_outcome_key)
