#!/usr/bin/env python3
"""Reward beam probe with native target/player relative-motion ranking.

V3 fixed the purely 1-D objective by considering target X/Y geometry, but the
exact ``star-visible-v24`` replay exposed a third failure mode: once the Star
entered state 0x80, Mario passed near it while still carrying strong leftward
momentum.  The Star then moved right and the beam kept selecting momentarily
close states that were already diverging.

V4 preserves V2 sticky tracking, V3 exact-Mesen 2-D search, and the base timeout
containment.  It augments each completed chunk with the PowerUpObject's native
SMB speed/move-force bytes and ranks active targets by relative closing motion
before instantaneous distance.  Native capability state remains the only
collection proof.
"""

from __future__ import annotations

from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# Import V3 first: this also installs V2 sticky tracking and native Mario speeds.
import smb1_reward_beam_probe_v3 as _v3  # noqa: F401
import smb1_reward_beam_probe as base

from fami_pixel.games.smb1.reward_beam import matching_reward
from fami_pixel.games.smb1.reward_motion import reward_intercept_key_motion
from fami_pixel.games.smb1.reward_target import read_active_reward_target


_original_simulate_chunk = base._simulate_chunk


def _simulate_chunk_motion(core, chunk, **kwargs):
    """Run the authoritative V3 chunk and attach native target motion state."""

    outcome = _original_simulate_chunk(core, chunk, **kwargs)
    tracked = read_active_reward_target(
        core,
        player_x=int(outcome.mario_x),
        behind_px=192,
        ahead_px=192,
    )
    object.__setattr__(outcome, "motion_reward", tracked)
    return outcome


def _node_from_outcome_motion(state_file, path, outcome, target_reward):
    reward = matching_reward(outcome.radar, target_reward)
    tracked = getattr(outcome, "motion_reward", None)
    if tracked is not None and str(tracked.get("type")) == str(target_reward):
        merged = {} if reward is None else dict(reward)
        merged.update(dict(tracked))
        reward = merged

    enemy = outcome.radar.get("nearest_enemy_dx")
    enemy_dx = None if enemy is None else int(enemy)
    key = reward_intercept_key_motion(
        reward=reward,
        nearest_enemy_dx=enemy_dx,
        mario_y=int(outcome.mario_y),
        player_x_speed=int(getattr(outcome, "player_x_speed", 0)),
        player_y_speed=int(getattr(outcome, "player_y_speed", 0)),
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
        object.__setattr__(node, "target_dy", int(reward.get("y", outcome.mario_y)) - int(outcome.mario_y))
        object.__setattr__(node, "target_x_speed", int(reward.get("x_speed", 0)))
        object.__setattr__(node, "target_y_speed", int(reward.get("y_speed", 0)))
    object.__setattr__(node, "player_x_speed", int(getattr(outcome, "player_x_speed", 0)))
    object.__setattr__(node, "player_y_speed", int(getattr(outcome, "player_y_speed", 0)))
    return node


base._simulate_chunk = _simulate_chunk_motion
base._node_from_outcome = _node_from_outcome_motion
# Keep supervisor children on this wrapper so every patch survives --worker.
base.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(base.main())
