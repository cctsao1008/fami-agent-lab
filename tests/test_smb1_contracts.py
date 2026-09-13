from fami_pixel.adapters.mesen import NES_A, NES_LEFT, NES_RIGHT
from fami_pixel.games.smb1 import (
    ActionCommand,
    Smb1Action,
    Smb1State,
    action_to_nes_buttons,
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


def test_action_mapping_matches_native_nes_bytes() -> None:
    assert action_to_nes_buttons(Smb1Action.NOOP) == 0x00
    assert action_to_nes_buttons(Smb1Action.RIGHT) == NES_RIGHT
    assert action_to_nes_buttons(Smb1Action.RIGHT_A) == (NES_RIGHT | NES_A)
    assert action_to_nes_buttons(Smb1Action.LEFT) == NES_LEFT
    assert action_to_nes_buttons(Smb1Action.A) == NES_A


def test_action_command_is_frame_explicit() -> None:
    command = ActionCommand(Smb1Action.RIGHT_A, 10)
    assert command.frame_count == 10
    assert command.nes_buttons == (NES_RIGHT | NES_A)


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
