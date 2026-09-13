"""Structured SMB1 observation contract for the M1 environment layer."""

from __future__ import annotations

from dataclasses import dataclass

from fami_pixel.adapters.mesen import MesenCore

from .state import Smb1State, read_smb1_state


@dataclass(frozen=True)
class Smb1Observation:
    """Stable structured projection derived from authoritative SMB1 RAM state."""

    native_frame_id: int
    smb_frame_counter: int
    world: int
    level: int
    mario_x_abs: int
    mario_y: int
    mario_y_high: int
    player_state: int
    player_x_speed: int
    player_y_speed: int
    raw_joypad: int
    oper_mode: int
    oper_mode_task: int
    game_engine_subroutine: int

    @property
    def world_display(self) -> int:
        return self.world + 1

    @property
    def level_display(self) -> int:
        return self.level + 1

    @property
    def is_player_control(self) -> bool:
        return self.game_engine_subroutine == 0x08 and self.oper_mode == 1


def observation_from_state(native_frame_id: int, state: Smb1State) -> Smb1Observation:
    if native_frame_id < 0:
        raise ValueError("native_frame_id must be >= 0")

    return Smb1Observation(
        native_frame_id=native_frame_id,
        smb_frame_counter=state.frame_counter,
        world=state.world,
        level=state.level,
        mario_x_abs=state.player_absolute_x,
        mario_y=state.player_y,
        mario_y_high=state.player_y_high,
        player_state=state.player_state,
        player_x_speed=state.player_x_speed,
        player_y_speed=state.player_y_speed,
        raw_joypad=state.saved_joypad1,
        oper_mode=state.oper_mode,
        oper_mode_task=state.oper_mode_task,
        game_engine_subroutine=state.game_engine_subroutine,
    )


def read_smb1_observation(core: MesenCore, native_frame_id: int) -> Smb1Observation:
    """Read authoritative SMB1 RAM state and project it into the M1 contract."""

    return observation_from_state(native_frame_id, read_smb1_state(core))
