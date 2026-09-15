#!/usr/bin/env python3
"""Reward beam probe with sticky active-target tracking.

The original beam probe intentionally reuses the normal forward-scene radar.
Field replay of ``star-visible-v24`` showed why that is insufficient for reward
interception: the Star starts at dx=-1 and, while Mario is still carrying right
momentum, falls outside the radar's dx>=-16 trailing window after only two
4-frame chunks.  Every branch then reports target_lost even though the active
PowerUpObject may still exist behind Mario.

This wrapper preserves the original exact-Mesen beam search and process
containment, but augments each radar payload with the active PowerUpObject from a
bounded 192 px trailing target tracker.  Hazard sensing remains forward-only.
"""

from __future__ import annotations

from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import smb1_reward_beam_probe as base

from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.games.smb1.reward_target import read_active_reward_target


TRAILING_TARGET_PX = 192
AHEAD_TARGET_PX = 192


def _sticky_reward_radar(core, mario_x: int) -> dict:
    payload = read_smb1_radar(core, player_x=mario_x).to_payload()
    tracked = read_active_reward_target(
        core,
        player_x=mario_x,
        behind_px=TRAILING_TARGET_PX,
        ahead_px=AHEAD_TARGET_PX,
    )
    payload["sticky_reward_target"] = tracked
    if tracked is None:
        return payload

    rewards = list(payload.get("rewards") or ())
    duplicate = any(
        int(item.get("slot", -1)) == int(tracked["slot"])
        and int(item.get("x", -1)) == int(tracked["x"])
        for item in rewards
    )
    if not duplicate:
        rewards.append(dict(tracked))
        rewards.sort(key=lambda item: abs(int(item.get("dx", 0))))
        payload["rewards"] = rewards
    return payload


# Patch only the observation surface used by the lab search.  The original
# module's supervisor constructs its child command from module.__file__, so point
# that back at this wrapper to preserve the sticky tracker in --worker children.
base._radar = _sticky_reward_radar
base.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(base.main())
