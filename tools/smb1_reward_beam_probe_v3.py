#!/usr/bin/env python3
"""Reward beam probe with sticky target tracking and 2-D interception ranking.

V2 fixed target-memory loss by tracking the active PowerUpObject behind Mario.
The deterministic Star replay then exposed a second failure mode: Mario could
match the Star's world X exactly while remaining vertically misaligned, after
which the active Star moved right and the 1-D beam settled into a trailing path.

This V3 laboratory wrapper preserves V2 sticky tracking and the base exact-Mesen
beam machinery, but ranks child states using target X/Y geometry plus Mario's
native horizontal speed. Mesen capability state remains the only collection
proof.
"""

from __future__ import annotations

from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# Import V2 first for its sticky reward-target observation patch.
import smb1_reward_beam_probe_v2 as _v2  # noqa: F401
import smb1_reward_beam_probe as base

from fami_pixel.games.smb1 import observation_from_state, read_smb1_state
from fami_pixel.games.smb1.reward_beam import (
    matching_reward,
    reward_intercept_key_2d,
)


_original_simulate_chunk = base._simulate_chunk


def _simulate_chunk_2d(core, chunk, **kwargs):
    """Run the authoritative base chunk and attach native Mario momentum."""

    outcome = _original_simulate_chunk(core, chunk, **kwargs)
    obs = observation_from_state(core.frame_count(), read_smb1_state(core))
    # Base ChunkOutcome is frozen but not slotted; these diagnostic attributes do
    # not change its public contract and are consumed only by this V3 wrapper.
    object.__setattr__(outcome, "player_x_speed", int(obs.player_x_speed))
    object.__setattr__(outcome, "player_y_speed", int(obs.player_y_speed))
    return outcome


def _node_from_outcome_2d(state_file, path, outcome, target_reward):
    reward = matching_reward(outcome.radar, target_reward)
    enemy = outcome.radar.get("nearest_enemy_dx")
    enemy_dx = None if enemy is None else int(enemy)
    key = reward_intercept_key_2d(
        reward=reward,
        nearest_enemy_dx=enemy_dx,
        mario_x=int(outcome.mario_x),
        mario_y=int(outcome.mario_y),
        player_x_speed=int(getattr(outcome, "player_x_speed", 0)),
    )
    node = base.BeamNode(
        state_file=state_file,
        path=path,
        frame=outcome.frame,
        mario_x=outcome.mario_x,
        mario_y=outcome.mario_y,
        key=key,
        target_dx=None if reward is None else int(reward["dx"]),
        target_state=None if reward is None else int(reward.get("state", 0)),
        nearest_enemy_dx=enemy_dx,
    )
    if reward is not None:
        object.__setattr__(node, "target_y", int(reward.get("y", outcome.mario_y)))
        object.__setattr__(
            node,
            "target_dy",
            int(reward.get("y", outcome.mario_y)) - int(outcome.mario_y),
        )
    object.__setattr__(node, "player_x_speed", int(getattr(outcome, "player_x_speed", 0)))
    object.__setattr__(node, "player_y_speed", int(getattr(outcome, "player_y_speed", 0)))
    return node


base._simulate_chunk = _simulate_chunk_2d
base._node_from_outcome = _node_from_outcome_2d
# Keep the supervisor child on this wrapper so both sticky tracking and 2-D
# ranking survive the worker-process boundary.
base.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(base.main())
