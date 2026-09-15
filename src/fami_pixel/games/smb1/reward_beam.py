"""Bounded action-chunk vocabulary and ranking for reward interception search.

This module is intentionally policy-only. Mesen remains the transition and
terminal-event authority. The helpers here define a small auditable chunk set
for a deterministic beam probe and rank *alive intermediate states* toward an
explicit reward target without letting forward progress dominate collection.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import ActionCommand, Smb1Action


@dataclass(frozen=True)
class RewardBeamChunk:
    name: str
    commands: tuple[ActionCommand, ...]

    @property
    def frame_count(self) -> int:
        return sum(command.frame_count for command in self.commands)


# Four-frame chunks keep the search aligned with the live control quantum while
# allowing the beam to synthesize longer braking / waiting / backtracking paths.
REWARD_BEAM_CHUNKS: tuple[RewardBeamChunk, ...] = (
    RewardBeamChunk("coast4", (ActionCommand(Smb1Action.NOOP, 4),)),
    RewardBeamChunk("right4", (ActionCommand(Smb1Action.RIGHT_B, 4),)),
    RewardBeamChunk("left4", (ActionCommand(Smb1Action.LEFT_B, 4),)),
    RewardBeamChunk(
        "rearm_right_jump4",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 3),
        ),
    ),
    RewardBeamChunk(
        "rearm_left_jump4",
        (
            ActionCommand(Smb1Action.LEFT_B, 1),
            ActionCommand(Smb1Action.LEFT_A_B, 3),
        ),
    ),
    RewardBeamChunk(
        "release_then_left4",
        (
            ActionCommand(Smb1Action.NOOP, 2),
            ActionCommand(Smb1Action.LEFT_B, 2),
        ),
    ),
)

# The original six-chunk vocabulary can *start* a jump but cannot compose a
# long button hold across control quanta: every rearm chunk releases A on its
# first frame.  The star-visible-v24 replay showed that this matters in practice:
# the active Star remained 20-50 px above Mario while V3/V4 repeatedly generated
# short rearmed jumps.  Keep the old tuple stable for historical probe
# reproducibility and expose an explicit extended vocabulary for V5+ searches.
REWARD_BEAM_CHUNKS_WITH_HOLD: tuple[RewardBeamChunk, ...] = REWARD_BEAM_CHUNKS + (
    RewardBeamChunk("hold_right_jump4", (ActionCommand(Smb1Action.RIGHT_A_B, 4),)),
    RewardBeamChunk("hold_left_jump4", (ActionCommand(Smb1Action.LEFT_A_B, 4),)),
)


def buttons_for_chunk_frame(chunk: RewardBeamChunk, frame_offset: int) -> int:
    """Return the exact NES button byte for one frame inside a chunk."""

    remaining = int(frame_offset)
    if remaining < 0 or remaining >= chunk.frame_count:
        raise ValueError("frame_offset must be within chunk")
    for command in chunk.commands:
        if remaining < command.frame_count:
            return command.nes_buttons
        remaining -= command.frame_count
    raise AssertionError("unreachable chunk frame")


def matching_reward(radar: dict, reward_type: str) -> dict | None:
    """Return the nearest currently visible reward of the requested type."""

    matches: list[dict] = []
    for reward in radar.get("rewards") or ():
        if str(reward.get("type")) != reward_type:
            continue
        try:
            dx = int(reward.get("dx"))
        except (TypeError, ValueError):
            continue
        item = dict(reward)
        item["dx"] = dx
        matches.append(item)
    if not matches:
        return None
    return min(matches, key=lambda item: abs(int(item["dx"])))


def reward_collection_proven(
    reward_type: str,
    *,
    baseline_player_status: int,
    baseline_star_timer: int,
    radar: dict,
) -> bool:
    """Use native capability state only; object disappearance is not collection."""

    if reward_type in {"mushroom", "fire_flower"}:
        return int(radar.get("player_status", 0)) > int(baseline_player_status)
    if reward_type == "star":
        return int(radar.get("star_invincible_timer", 0)) > int(baseline_star_timer)
    # The radar does not yet expose a lives counter, so 1-Up remains unproven.
    return False


def reward_object_is_active(reward: dict | None) -> bool:
    """Return whether the native PowerUpObject has entered its active phase.

    The deterministic V24 Star replay exposed a useful state transition:
    ``Enemy_State`` advances through small emergence-counter values (2..17)
    while the item rises from the block, then switches to ``0x80`` when the
    active object phase begins.  Treating every state >= 3 as active incorrectly
    rewarded still-emerging states.  Bit 7 is therefore used only as a ranking
    hint here; collection remains proven exclusively by capability state.
    """

    if reward is None:
        return False
    try:
        state = int(reward.get("state", 0)) & 0xFF
    except (TypeError, ValueError):
        return False
    return bool(state & 0x80)


def _signed_byte(value: int) -> int:
    value = int(value) & 0xFF
    return value - 0x100 if value & 0x80 else value


def reward_intercept_key_2d(
    *,
    reward: dict | None,
    nearest_enemy_dx: int | None,
    mario_x: int,
    mario_y: int,
    player_x_speed: int = 0,
) -> tuple[int, int, int, int, int, int, int]:
    """Rank an interception frontier using native X/Y geometry plus momentum.

    The V24 Star replay demonstrated why ``abs(dx)`` alone is insufficient. At
    depth 16 Mario and the Star both reached world X=1616, yet collection did not
    occur; after the Star entered state ``0x80`` it moved right and the 1-D beam
    settled into a ~28 px trailing state.  The missing variable is the vertical
    interception geometry, with horizontal momentum useful only as a secondary
    closing hint.

    Ordering is deliberately lexicographic and remains non-authoritative:

    1. keep the target tracked,
    2. prefer a natively active/released target,
    3. prefer a loose 2-D overlap envelope,
    4. reduce Manhattan X/Y separation,
    5. prefer horizontal velocity that closes the current X error,
    6. retain enemy clearance,
    7. reduce horizontal separation as the final deterministic tie-breaker.

    The loose overlap envelope is only a search hint. Collection is still proven
    exclusively by ``reward_collection_proven()`` from native capability state.
    """

    if reward is None:
        return (0, 0, 0, -1_000_000, -1_000_000, -1_000_000, -1_000_000)

    dx = int(reward.get("dx", 0))
    try:
        reward_y = int(reward.get("y", mario_y))
    except (TypeError, ValueError):
        reward_y = int(mario_y)
    dy = reward_y - int(mario_y)
    active = 1 if reward_object_is_active(reward) else 0

    # SMB1 sprites are roughly one tile in this interaction. Keep this envelope
    # intentionally loose: it is for beam ordering only, never collision proof.
    overlap_hint = 1 if abs(dx) <= 16 and abs(dy) <= 24 else 0
    spatial_distance = abs(dx) + abs(dy)

    vx = _signed_byte(player_x_speed)
    if dx > 4:
        closing = vx
    elif dx < -4:
        closing = -vx
    else:
        # Near horizontal alignment, do not invent a target-velocity model.
        closing = 0

    if nearest_enemy_dx is None:
        clearance = 255
    else:
        enemy_dx = int(nearest_enemy_dx)
        clearance = max(0, min(255, enemy_dx)) if enemy_dx >= 0 else 255

    return (
        1,
        active,
        overlap_hint,
        -spatial_distance,
        closing,
        clearance,
        -abs(dx),
    )


def reward_beam_key(
    *,
    reward: dict | None,
    nearest_enemy_dx: int | None,
    mario_x: int,
) -> tuple[int, int, int, int, int]:
    """Rank alive intermediate states for the original 1-D COLLECT probe.

    This key is retained for the V1/V2 laboratory probes and backward-compatible
    tests. New interception work should prefer ``reward_intercept_key_2d`` so a
    horizontal crossing is not mistaken for an actual collection opportunity.
    """

    if reward is None:
        return (0, 0, -1_000_000, -1_000_000, -int(mario_x))

    dx = int(reward.get("dx", 0))
    active = 1 if reward_object_is_active(reward) else 0

    if nearest_enemy_dx is None:
        clearance = 255
    else:
        enemy_dx = int(nearest_enemy_dx)
        # Enemies behind us should not beat a safely distant forward enemy just
        # because abs(dx) is large. Forward positive clearance is what matters.
        clearance = max(0, min(255, enemy_dx)) if enemy_dx >= 0 else 255

    return (1, active, -abs(dx), clearance, -int(mario_x))
