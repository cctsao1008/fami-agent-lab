#!/usr/bin/env python3
"""Reward beam probe with composable jump-hold chunks.

V4 added native relative-motion ranking, but ``star-visible-v24`` still selected
essentially the same path as V3 and failed to collect.  The per-frame replay
exposed a more basic action-vocabulary limitation: the historical six-chunk
beam can start a jump with three frames of A, but every subsequent rearm chunk
releases A on its first frame.  It therefore cannot express the long continuous
A hold already known to produce higher SMB1 jumps.

V5 preserves V4's sticky target tracking, 2-D/native-motion ranking, exact Mesen
rollouts, and timeout containment.  It changes only the search vocabulary by
adding four-frame LEFT+A+B and RIGHT+A+B hold chunks.  Collection remains proven
only by native capability state.
"""

from __future__ import annotations

from pathlib import Path
import sys

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# Import V4 first so sticky tracking, 2-D ranking, and native target motion are
# all installed before we replace only the candidate vocabulary.
import smb1_reward_beam_probe_v4 as _v4  # noqa: F401
import smb1_reward_beam_probe as base

from fami_pixel.games.smb1.reward_beam import REWARD_BEAM_CHUNKS_WITH_HOLD


base.REWARD_BEAM_CHUNKS = REWARD_BEAM_CHUNKS_WITH_HOLD
# Keep supervisor children on this wrapper so the extended vocabulary survives
# the --worker process boundary.
base.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(base.main())
