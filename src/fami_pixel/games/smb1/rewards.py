"""Explicit reward semantics for SMB1 live planning.

These helpers turn the native reward-radar payload into a small auditable
state-dependent utility. They do not create machine truth and never override
immediate Mesen safety evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_REWARD_LOOKAHEAD_PX = 192
DEFAULT_REWARD_PURSUIT_PX = 128
DEFAULT_MIN_REWARD_UTILITY = 0.35

_BASE_REWARD_VALUE = {
    "mushroom": 1.00,
    "fire_flower": 1.10,
    "star": 1.40,
    "one_up": 0.80,
}


@dataclass(frozen=True)
class RewardOpportunity:
    reward_type: str
    dx: int
    y: int
    utility: float
    slot: int | None = None


def state_dependent_reward_value(
    reward_type: str,
    *,
    player_status: int,
    star_invincible_timer: int,
) -> float:
    """Return the capability-conditioned marginal value of one reward type."""
    reward_type = str(reward_type)
    base = float(_BASE_REWARD_VALUE.get(reward_type, 0.0))

    if reward_type == "mushroom":
        return base if int(player_status) == 0 else 0.35
    if reward_type == "fire_flower":
        return base if int(player_status) < 2 else 0.15
    if reward_type == "star":
        # A fresh Star is very valuable; while already strongly invincible the
        # marginal value is lower but still non-zero.
        return base if int(star_invincible_timer) <= 4 else 0.30
    if reward_type == "one_up":
        return base
    return base


def reward_distance_discount(dx: int, *, lookahead_px: int = DEFAULT_REWARD_LOOKAHEAD_PX) -> float:
    """Favor nearby rewards while keeping edge-of-lookahead rewards visible."""
    if lookahead_px <= 0:
        raise ValueError("lookahead_px must be > 0")
    distance = max(0, int(dx))
    if distance >= lookahead_px:
        return 0.25
    return max(0.25, 1.0 - 0.75 * (distance / lookahead_px))


def best_reward_opportunity(radar: dict) -> RewardOpportunity | None:
    """Select the highest current reward opportunity from one radar payload."""
    rewards = list(radar.get("rewards") or [])
    if not rewards:
        return None

    player_status = int(radar.get("player_status", 0))
    star_timer = int(radar.get("star_invincible_timer", 0))
    lookahead = max(1, int(radar.get("lookahead_px", DEFAULT_REWARD_LOOKAHEAD_PX)))

    opportunities: list[RewardOpportunity] = []
    for reward in rewards:
        dx = int(reward.get("dx", 0))
        if dx < -16:
            continue
        reward_type = str(reward.get("type") or "unknown")
        base = state_dependent_reward_value(
            reward_type,
            player_status=player_status,
            star_invincible_timer=star_timer,
        )
        utility = base * reward_distance_discount(dx, lookahead_px=lookahead)
        opportunities.append(
            RewardOpportunity(
                reward_type=reward_type,
                dx=dx,
                y=int(reward.get("y", 0)),
                utility=utility,
                slot=None if reward.get("slot") is None else int(reward["slot"]),
            )
        )

    if not opportunities:
        return None
    return max(opportunities, key=lambda item: (item.utility, -max(item.dx, 0)))


def should_pursue_reward(
    radar: dict,
    *,
    max_dx: int = DEFAULT_REWARD_PURSUIT_PX,
    min_utility: float = DEFAULT_MIN_REWARD_UTILITY,
) -> RewardOpportunity | None:
    """Return a bounded reward target, or None when pursuit should stay inactive."""
    opportunity = best_reward_opportunity(radar)
    if opportunity is None:
        return None
    if opportunity.dx < -16 or opportunity.dx > int(max_dx):
        return None
    if opportunity.utility < float(min_utility):
        return None
    return opportunity


def immediate_mesen_safe(plan: dict) -> bool:
    """Use existing rollout evidence as the non-negotiable reward safety gate."""
    if str(plan.get("terminal", "none")) == "death":
        return False
    score = list(plan.get("score") or [])
    return not score or float(score[0]) >= 0.0


def select_reward_preferred_plan(
    plans: list[dict],
    radar: dict,
    *,
    risk_cutoff: float,
    jump_names: frozenset[str] = frozenset({"rearm_jump_8", "rearm_jump_12"}),
    run_names: frozenset[str] = frozenset({"run_8", "right_8"}),
) -> tuple[dict | None, RewardOpportunity | None]:
    """Choose a reward-seeking action only inside the immediate-safe set.

    The helper is deliberately conservative. If no immediate-safe candidate is
    also below the learned-risk cutoff, it declines to override the normal live
    planner. This keeps reward preference bounded and auditable.
    """
    opportunity = should_pursue_reward(radar)
    if opportunity is None:
        return None, None

    safe = [plan for plan in plans if immediate_mesen_safe(plan)]
    under_cutoff = [
        plan
        for plan in safe
        if float(plan.get("risk_probability", 1.0)) <= float(risk_cutoff)
    ]
    if not under_cutoff:
        return None, opportunity

    reward_is_elevated = opportunity.y < 168
    prefer_jump = opportunity.reward_type in {"star", "fire_flower"} or reward_is_elevated
    preferred_names = jump_names if prefer_jump else run_names
    preferred = [
        plan for plan in under_cutoff if str(plan.get("candidate")) in preferred_names
    ]
    eligible = preferred or under_cutoff

    def plan_key(plan: dict) -> tuple[float, float, float]:
        score = list(plan.get("score") or [])
        progress = float(plan.get("progress", score[2] if len(score) > 2 else 0.0))
        max_x = float(score[3]) if len(score) > 3 else progress
        risk = float(plan.get("risk_probability", 1.0))
        return (progress, max_x, -risk)

    return max(eligible, key=plan_key), opportunity
