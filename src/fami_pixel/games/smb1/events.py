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
    DIED = "died"
    LEVEL_COMPLETED = "level_completed"


@dataclass(frozen=True)
class GameEvent:
    kind: GameEventType
    frame_id: int
    delta_x: int = 0


_GROUNDED_PLAYER_STATE = 0
_JUMP_PLAYER_STATE = 1

# SMB1 GameRoutines dispatch + machine evidence:
#   $05 -> PlayerEndLevel
#   $06 -> PlayerLoseLife
#   $0B -> PlayerDeath
#
# Enemy collision validation entered $0B directly. A later World 1-1 traversal
# showed pit/fall deaths can enter $06 directly from $08, bypassing $0B.
# Therefore DIED has two authoritative entry paths. The $06 guard excludes
# $0B->$06 so the bookkeeping transition does not emit a duplicate death.
_PLAYER_END_LEVEL_SUBROUTINE = 0x05
_PLAYER_LOSE_LIFE_SUBROUTINE = 0x06
_PLAYER_DEATH_SUBROUTINE = 0x0B


def derive_game_events(
    previous: Smb1Observation,
    current: Smb1Observation,
) -> tuple[GameEvent, ...]:
    """Derive validated events from two consecutive structured observations."""

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

    previous_engine = previous.game_engine_subroutine
    current_engine = current.game_engine_subroutine

    entered_player_death = (
        previous_engine != _PLAYER_DEATH_SUBROUTINE
        and current_engine == _PLAYER_DEATH_SUBROUTINE
    )
    entered_direct_lose_life = (
        previous_engine not in (_PLAYER_DEATH_SUBROUTINE, _PLAYER_LOSE_LIFE_SUBROUTINE)
        and current_engine == _PLAYER_LOSE_LIFE_SUBROUTINE
    )

    if entered_player_death or entered_direct_lose_life:
        events.append(
            GameEvent(
                kind=GameEventType.DIED,
                frame_id=current.native_frame_id,
            )
        )

    if (
        previous_engine != _PLAYER_END_LEVEL_SUBROUTINE
        and current_engine == _PLAYER_END_LEVEL_SUBROUTINE
    ):
        events.append(
            GameEvent(
                kind=GameEventType.LEVEL_COMPLETED,
                frame_id=current.native_frame_id,
            )
        )

    return tuple(events)
