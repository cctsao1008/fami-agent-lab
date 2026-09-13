"""Super Mario Bros. game-state decoder for the validated M0 ROM path.

Addresses are taken from the public SMB disassembly family and intentionally
live outside the generic Mesen adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

from fami_pixel.adapters.mesen import MesenCore, read_nes_cpu_memory


ADDR_FRAME_COUNTER = 0x0009
ADDR_GAME_ENGINE_SUBROUTINE = 0x000E
ADDR_PLAYER_STATE = 0x001D
ADDR_PLAYER_X_SPEED = 0x0057
ADDR_PLAYER_PAGE = 0x006D
ADDR_PLAYER_X = 0x0086
ADDR_PLAYER_Y_SPEED = 0x009F
ADDR_PLAYER_Y_HIGH = 0x00B5
ADDR_PLAYER_Y = 0x00CE
ADDR_SAVED_JOYPAD1 = 0x06FC
ADDR_OPER_MODE = 0x0770
ADDR_OPER_MODE_TASK = 0x0772
ADDR_LEVEL_NUMBER = 0x075C
ADDR_WORLD_NUMBER = 0x075F

TITLE_SCREEN_MODE = 0
GAME_MODE = 1
PLAYER_CONTROL_SUBROUTINE = 0x08


@dataclass(frozen=True)
class Smb1State:
    frame_counter: int
    oper_mode: int
    oper_mode_task: int
    game_engine_subroutine: int
    world: int
    level: int
    player_page: int
    player_x: int
    player_y_high: int
    player_y: int
    player_state: int
    player_x_speed: int
    player_y_speed: int
    saved_joypad1: int

    @property
    def player_absolute_x(self) -> int:
        return (self.player_page << 8) | self.player_x

    @property
    def is_title_menu(self) -> bool:
        return self.oper_mode == TITLE_SCREEN_MODE and self.oper_mode_task == 3

    @property
    def is_world_1_1_player_control(self) -> bool:
        return (
            self.oper_mode == GAME_MODE
            and self.world == 0
            and self.level == 0
            and self.game_engine_subroutine == PLAYER_CONTROL_SUBROUTINE
            and self.player_y_high == 1
        )


def read_smb1_state(core: MesenCore) -> Smb1State:
    """Read the small SMB1 RAM surface needed by the M0 gameplay witness."""

    read = lambda addr: read_nes_cpu_memory(core, addr)
    return Smb1State(
        frame_counter=read(ADDR_FRAME_COUNTER),
        oper_mode=read(ADDR_OPER_MODE),
        oper_mode_task=read(ADDR_OPER_MODE_TASK),
        game_engine_subroutine=read(ADDR_GAME_ENGINE_SUBROUTINE),
        world=read(ADDR_WORLD_NUMBER),
        level=read(ADDR_LEVEL_NUMBER),
        player_page=read(ADDR_PLAYER_PAGE),
        player_x=read(ADDR_PLAYER_X),
        player_y_high=read(ADDR_PLAYER_Y_HIGH),
        player_y=read(ADDR_PLAYER_Y),
        player_state=read(ADDR_PLAYER_STATE),
        player_x_speed=read(ADDR_PLAYER_X_SPEED),
        player_y_speed=read(ADDR_PLAYER_Y_SPEED),
        saved_joypad1=read(ADDR_SAVED_JOYPAD1),
    )
