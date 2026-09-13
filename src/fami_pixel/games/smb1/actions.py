"""Logical SMB1 action contract for the M1 environment layer.

These actions are game-facing abstractions. They are translated to exact NES
controller bytes by the environment and do not replace Mesen's native input
provider as the execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fami_pixel.adapters.mesen import NES_A, NES_B, NES_LEFT, NES_RIGHT


class Smb1Action(str, Enum):
    NOOP = "NOOP"
    A = "A"
    B = "B"
    RIGHT = "RIGHT"
    RIGHT_A = "RIGHT_A"
    RIGHT_B = "RIGHT_B"
    RIGHT_A_B = "RIGHT_A_B"
    LEFT = "LEFT"
    LEFT_A = "LEFT_A"
    LEFT_B = "LEFT_B"
    LEFT_A_B = "LEFT_A_B"


_ACTION_TO_BUTTONS: dict[Smb1Action, int] = {
    Smb1Action.NOOP: 0x00,
    Smb1Action.A: NES_A,
    Smb1Action.B: NES_B,
    Smb1Action.RIGHT: NES_RIGHT,
    Smb1Action.RIGHT_A: NES_RIGHT | NES_A,
    Smb1Action.RIGHT_B: NES_RIGHT | NES_B,
    Smb1Action.RIGHT_A_B: NES_RIGHT | NES_A | NES_B,
    Smb1Action.LEFT: NES_LEFT,
    Smb1Action.LEFT_A: NES_LEFT | NES_A,
    Smb1Action.LEFT_B: NES_LEFT | NES_B,
    Smb1Action.LEFT_A_B: NES_LEFT | NES_A | NES_B,
}


@dataclass(frozen=True)
class ActionCommand:
    """Apply one logical SMB1 action for an exact number of emulator frames."""

    action: Smb1Action
    frame_count: int

    def __post_init__(self) -> None:
        if self.frame_count <= 0:
            raise ValueError("frame_count must be > 0")

    @property
    def nes_buttons(self) -> int:
        return _ACTION_TO_BUTTONS[self.action]


def action_to_nes_buttons(action: Smb1Action) -> int:
    """Translate a logical SMB1 action to the exact native NES controller byte."""

    return _ACTION_TO_BUTTONS[action]
