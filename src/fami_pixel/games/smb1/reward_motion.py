"""Native relative-motion ranking for SMB1 reward interception.

The exact Mesen beam remains the transition authority.  This module only ranks
alive frontier states.  It uses the native SMB object speed bytes exposed by the
sticky reward tracker so a state that is momentarily close but rapidly crossing
away from a Star does not beat a slightly farther state whose relative motion is
actually converging.
"""

from __future__ import annotations

from .reward_beam import reward_object_is_active


def signed_byte(value: int) -> int:
    """Decode an SMB signed 8-bit speed byte."""

    value = int(value) & 0xFF
    return value - 0x100 if value & 0x80 else value


def relative_closing_score(delta: int, *, target_speed: int, player_speed: int, near_px: int = 4) -> int:
    """Return a positive score when relative motion closes one coordinate.

    ``delta`` is target minus Mario.  The SMB speed fields are signed bytes.  We
    intentionally compare them in native units rather than pretending this hint
    is a physics model; exact future behavior still comes from Mesen rollouts.

    Near alignment, high relative speed is penalized because a fast crossing is
    exactly the failure observed in the V3 Star replay.
    """

    delta = int(delta)
    rel_speed = signed_byte(target_speed) - signed_byte(player_speed)
    if abs(delta) <= max(0, int(near_px)):
        return -abs(rel_speed)
    if delta < 0:
        # Target is left/up of Mario; increasing relative coordinate closes it.
        return rel_speed
    # Target is right/down of Mario; decreasing relative coordinate closes it.
    return -rel_speed


def reward_intercept_key_motion(
    *,
    reward: dict | None,
    nearest_enemy_dx: int | None,
    mario_y: int,
    player_x_speed: int = 0,
    player_y_speed: int = 0,
) -> tuple[int, int, int, int, int, int, int, int, int]:
    """Rank a reward frontier by active-state geometry and relative motion.

    Ordering is deliberately lexicographic and non-authoritative:

    1. keep the target tracked,
    2. prefer the active/released power-up phase,
    3. once active, prefer both axes converging rather than merely close,
    4. avoid axes that are already diverging,
    5. retain the loose current 2-D overlap hint,
    6. prefer stronger net closing motion,
    7. reduce current Manhattan separation,
    8. retain forward-enemy clearance,
    9. reduce horizontal separation as a deterministic tie-breaker.

    Collection itself is never inferred from this key.
    """

    if reward is None:
        return (0, 0, 0, -2, 0, -1_000_000, -1_000_000, -1_000_000, -1_000_000)

    dx = int(reward.get("dx", 0))
    try:
        target_y = int(reward.get("y", mario_y))
    except (TypeError, ValueError):
        target_y = int(mario_y)
    dy = target_y - int(mario_y)
    active = 1 if reward_object_is_active(reward) else 0

    overlap_hint = 1 if abs(dx) <= 16 and abs(dy) <= 24 else 0
    spatial_distance = abs(dx) + abs(dy)

    if active:
        x_close = relative_closing_score(
            dx,
            target_speed=int(reward.get("x_speed", 0)),
            player_speed=int(player_x_speed),
        )
        y_close = relative_closing_score(
            dy,
            target_speed=int(reward.get("y_speed", 0)),
            player_speed=int(player_y_speed),
        )
        closing_axes = int(x_close > 0) + int(y_close > 0)
        diverging_axes = int(x_close < 0) + int(y_close < 0)
        motion_balance = x_close + y_close
    else:
        # During the emergence counter phase the movement fields do not yet
        # describe the released Star trajectory. Preserve V3's geometry ranking.
        closing_axes = 0
        diverging_axes = 0
        motion_balance = 0

    if nearest_enemy_dx is None:
        clearance = 255
    else:
        enemy_dx = int(nearest_enemy_dx)
        clearance = max(0, min(255, enemy_dx)) if enemy_dx >= 0 else 255

    return (
        1,
        active,
        closing_axes,
        -diverging_axes,
        overlap_hint,
        motion_balance,
        -spatial_distance,
        clearance,
        -abs(dx),
    )
