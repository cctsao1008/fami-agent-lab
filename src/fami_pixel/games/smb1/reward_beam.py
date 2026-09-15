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


def reward_beam_key(
    *,
    reward: dict | None,
    nearest_enemy_dx: int | None,
    mario_x: int,
) -> tuple[int, int, int, int, int]:
    """Rank alive intermediate states for COLLECT without progress domination.

    Ordering is deliberately lexicographic:

    1. keep the target visible,
    2. prefer an active/spawned target state,
    3. reduce absolute interception distance,
    4. retain clearance from an enemy immediately ahead,
    5. use Mario X only as a final deterministic tie-breaker.

    A collected reward is handled as a terminal success by the caller and never
    reaches this intermediate-state ranking.
    """

    if reward is None:
        return (0, 0, -1_000_000, -1_000_000, -int(mario_x))

    dx = int(reward.get("dx", 0))
    try:
        state = int(reward.get("state", 0))
    except (TypeError, ValueError):
        state = 0

    # SMB1's active PowerUpObject is observed as state 3 in the current field
    # evidence. Do not make state >=3 a collection claim; it is only a ranking
    # hint that waiting near an emerging item can be useful.
    active = 1 if state >= 3 else 0

    if nearest_enemy_dx is None:
        clearance = 255
    else:
        enemy_dx = int(nearest_enemy_dx)
        # Enemies behind us should not beat a safely distant forward enemy just
        # because abs(dx) is large. Forward positive clearance is what matters.
        clearance = max(0, min(255, enemy_dx)) if enemy_dx >= 0 else 255

    return (1, active, -abs(dx), clearance, -int(mario_x))
