"""Derived SMB1 game events for the M1 environment layer.

These events are projections from consecutive structured observations. They are
not new machine authority: Mesen and the SMB1 decoder remain the sources of
truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .observation import Smb1Observation


class GameEventType(str, Enum):
    MOVED = "moved"
    JUMP_STARTED = "jump_started"
    LANDED = "landed"


@dataclass(frozen=True)
class GameEvent:
    kind: GameEventType
    frame_id: int
    delta_x: int = 0


# Narrow M1 heuristic grounded by the M0 gameplay witness:
# player_state 0 was observed while grounded and player_state 1 while jumping.
# Do not generalize other player_state values into airborne semantics yet.
_GROUNDED_PLAYER_STATE = 0
_JUMP_PLAYER_STATE = 1


def derive_game_events(
    previous: Smb1Observation,
    current: Smb1Observation,
) -> tuple[GameEvent, ...]:
    """Derive the initial validated event vocabulary from two observations.

    Only movement and the 0<->1 player-state transition are interpreted here.
    Death, level completion, coins, power-ups, damage, and enemy interactions
    require separately verified SMB1 state sources before becoming events.
    """

    if current.native_frame_id <= previous.native_frame_id:
        raise ValueError("current observation must be from a later native frame")

    events: list[GameEvent] = []
    delta_x = current.mario_x_abs - previous.mario_x_abs

    if delta_x != 0:
        events.append(
            GameEvent(
                kind=GameEventType.MOVED,
                frame_id=current.native_frame_id,
                delta_x=delta_x,
            )
        )

    if (
        previous.player_state == _GROUNDED_PLAYER_STATE
        and current.player_state == _JUMP_PLAYER_STATE
    ):
        events.append(
            GameEvent(
                kind=GameEventType.JUMP_STARTED,
                frame_id=current.native_frame_id,
            )
        )
    elif (
        previous.player_state == _JUMP_PLAYER_STATE
        and current.player_state == _GROUNDED_PLAYER_STATE
    ):
        events.append(
            GameEvent(
                kind=GameEventType.LANDED,
                frame_id=current.native_frame_id,
            )
        )

    return tuple(events)
