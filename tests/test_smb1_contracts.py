import pytest

from fami_pixel.adapters.mesen import NES_A, NES_LEFT, NES_RIGHT
from fami_pixel.games.smb1 import (
    ActionCommand,
    GameEventType,
    Smb1Action,
    Smb1State,
    action_to_nes_buttons,
    derive_game_events,
    observation_from_state,
)


def _state() -> Smb1State:
    return Smb1State(
        frame_counter=0x9F,
        oper_mode=1,
        oper_mode_task=3,
        game_engine_subroutine=0x08,
        world=0,
        level=0,
        player_page=0,
        player_x=0x28,
        player_y_high=1,
        player_y=0xB0,
        player_state=0,
        player_x_speed=0x18,
        player_y_speed=0,
        saved_joypad1=0,
    )


def _observation(
    frame_id: int,
    *,
    x: int,
    state: int,
    y: int = 0xB0,
    engine: int = 0x08,
):
    source = _state()
    source = Smb1State(
        frame_counter=source.frame_counter,
        oper_mode=source.oper_mode,
        oper_mode_task=source.oper_mode_task,
        game_engine_subroutine=engine,
        world=source.world,
        level=source.level,
        player_page=(x >> 8) & 0xFF,
        player_x=x & 0xFF,
        player_y_high=source.player_y_high,
        player_y=y,
        player_state=state,
        player_x_speed=source.player_x_speed,
        player_y_speed=source.player_y_speed,
        saved_joypad1=source.saved_joypad1,
    )
    return observation_from_state(frame_id, source)


def test_action_mapping_matches_native_nes_bytes() -> None:
    assert action_to_nes_buttons(Smb1Action.NOOP) == 0x00
    assert action_to_nes_buttons(Smb1Action.RIGHT) == NES_RIGHT
    assert action_to_nes_buttons(Smb1Action.RIGHT_A) == (NES_RIGHT | NES_A)
    assert action_to_nes_buttons(Smb1Action.LEFT) == NES_LEFT
    assert action_to_nes_buttons(Smb1Action.LEFT_A) == (NES_LEFT | NES_A)
    assert action_to_nes_buttons(Smb1Action.A) == NES_A


def test_action_command_is_frame_explicit() -> None:
    command = ActionCommand(Smb1Action.RIGHT_A, 10)
    assert command.frame_count == 10
    assert command.nes_buttons == (NES_RIGHT | NES_A)


def test_action_command_rejects_nonpositive_duration() -> None:
    with pytest.raises(ValueError):
        ActionCommand(Smb1Action.NOOP, 0)


def test_observation_projects_authoritative_state() -> None:
    observation = observation_from_state(196, _state())
    assert observation.native_frame_id == 196
    assert observation.smb_frame_counter == 0x9F
    assert observation.world_display == 1
    assert observation.level_display == 1
    assert observation.mario_x_abs == 40
    assert observation.mario_y == 0xB0
    assert observation.player_state == 0
    assert observation.raw_joypad == 0
    assert observation.is_player_control


def test_events_derive_movement_and_jump_start() -> None:
    previous = _observation(256, x=99, state=0)
    current = _observation(257, x=100, state=1, y=0xAC)
    events = derive_game_events(previous, current)
    assert [event.kind for event in events] == [GameEventType.MOVED, GameEventType.JUMP_STARTED]
    assert events[0].delta_x == 1
    assert events[0].frame_id == 257


def test_events_derive_landing() -> None:
    previous = _observation(280, x=130, state=1, y=0xA0)
    current = _observation(281, x=131, state=0, y=0xB0)
    events = derive_game_events(previous, current)
    assert [event.kind for event in events] == [GameEventType.MOVED, GameEventType.LANDED]


def test_events_derive_death_on_player_death_routine_entry() -> None:
    previous = _observation(300, x=140, state=0, engine=0x08)
    current = _observation(301, x=140, state=0, engine=0x0B)
    assert [event.kind for event in derive_game_events(previous, current)] == [GameEventType.DIED]


def test_events_derive_death_on_direct_lose_life_entry() -> None:
    previous = _observation(320, x=1542, state=1, y=0x04, engine=0x08)
    current = _observation(321, x=1542, state=1, y=0x04, engine=0x06)
    assert [event.kind for event in derive_game_events(previous, current)] == [GameEventType.DIED]


def test_player_death_to_lose_life_does_not_duplicate_death() -> None:
    previous = _observation(330, x=295, state=1, engine=0x0B)
    current = _observation(331, x=295, state=1, engine=0x06)
    assert derive_game_events(previous, current) == ()


def test_events_derive_level_complete_on_player_end_level_entry() -> None:
    previous = _observation(400, x=3000, state=3, engine=0x04)
    current = _observation(401, x=3000, state=3, engine=0x05)
    assert [event.kind for event in derive_game_events(previous, current)] == [GameEventType.LEVEL_COMPLETED]


def test_terminal_events_do_not_repeat_while_routine_is_unchanged() -> None:
    previous = _observation(500, x=140, state=0, engine=0x0B)
    current = _observation(501, x=140, state=0, engine=0x0B)
    assert derive_game_events(previous, current) == ()


def test_events_reject_nonmonotonic_frames() -> None:
    previous = _observation(257, x=100, state=0)
    current = _observation(257, x=101, state=0)
    with pytest.raises(ValueError):
        derive_game_events(previous, current)
