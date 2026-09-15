"""Sticky active reward tracking outside the forward-only scene radar.

The normal SMB1 radar intentionally clips objects behind Mario because it is a
forward-scene hazard/planning sensor.  Reward interception is different: once a
power-up is selected as an explicit target, the planner must keep tracking it
for a bounded trailing window while Mario brakes or turns around.

This module decodes the one active SMB1 PowerUpObject directly from the same
coherent 2 KiB internal-RAM surface, but does not reuse the forward-only
``dx >= -16`` visibility contract from ``radar.py``.
"""

from __future__ import annotations

from fami_pixel.adapters.mesen import MesenCore, read_nes_internal_ram

from .radar import (
    ADDR_ENEMY_FLAG,
    ADDR_ENEMY_ID,
    ADDR_ENEMY_PAGE,
    ADDR_ENEMY_STATE,
    ADDR_ENEMY_X,
    ADDR_ENEMY_Y,
    ADDR_ENEMY_Y_HIGH,
    ADDR_POWER_UP_TYPE,
    POWER_UP_NAMES,
    POWER_UP_OBJECT_ID,
    POWER_UP_SLOT,
)

# Native SMB object-motion fields from the canonical SMB1 disassembly family.
# Enemy arrays use slot-relative addressing; the power-up occupies slot 5.
ADDR_ENEMY_X_SPEED = 0x0058
ADDR_ENEMY_Y_SPEED = 0x00A0
ADDR_ENEMY_X_MOVE_FORCE = 0x0401
ADDR_ENEMY_Y_MOVE_FORCE = 0x0434


def decode_active_reward_target(
    ram: bytes,
    *,
    player_x: int,
    behind_px: int = 192,
    ahead_px: int = 192,
) -> dict | None:
    """Decode the active PowerUpObject in a bounded world-X window.

    ``behind_px`` is deliberately much larger than the normal forward-radar
    trailing allowance.  This is target tracking, not hazard sensing.

    The returned payload also preserves the native horizontal/vertical speed and
    subpixel movement-force bytes.  They are observation data only; Mesen remains
    the transition authority and collection is still proven from capability state.
    """

    if behind_px < 0 or ahead_px < 0:
        raise ValueError("behind_px and ahead_px must be >= 0")
    if len(ram) <= max(
        ADDR_ENEMY_FLAG + POWER_UP_SLOT,
        ADDR_ENEMY_ID + POWER_UP_SLOT,
        ADDR_ENEMY_STATE + POWER_UP_SLOT,
        ADDR_ENEMY_PAGE + POWER_UP_SLOT,
        ADDR_ENEMY_X + POWER_UP_SLOT,
        ADDR_ENEMY_Y_HIGH + POWER_UP_SLOT,
        ADDR_ENEMY_Y + POWER_UP_SLOT,
        ADDR_ENEMY_X_SPEED + POWER_UP_SLOT,
        ADDR_ENEMY_Y_SPEED + POWER_UP_SLOT,
        ADDR_ENEMY_X_MOVE_FORCE + POWER_UP_SLOT,
        ADDR_ENEMY_Y_MOVE_FORCE + POWER_UP_SLOT,
        ADDR_POWER_UP_TYPE,
    ):
        raise ValueError("RAM snapshot is too small for SMB1 reward tracking")

    slot = POWER_UP_SLOT
    if int(ram[ADDR_ENEMY_FLAG + slot]) == 0:
        return None
    if int(ram[ADDR_ENEMY_ID + slot]) != POWER_UP_OBJECT_ID:
        return None
    if int(ram[ADDR_ENEMY_Y_HIGH + slot]) != 1:
        return None

    power_up_type = int(ram[ADDR_POWER_UP_TYPE])
    world_x = (int(ram[ADDR_ENEMY_PAGE + slot]) << 8) | int(ram[ADDR_ENEMY_X + slot])
    dx = world_x - int(player_x)
    if dx < -int(behind_px) or dx > int(ahead_px):
        return None

    return {
        "slot": slot,
        "type": POWER_UP_NAMES.get(power_up_type, f"power_up_{power_up_type}"),
        "power_up_type": power_up_type,
        "state": int(ram[ADDR_ENEMY_STATE + slot]),
        "x": world_x,
        "y": int(ram[ADDR_ENEMY_Y + slot]),
        "dx": dx,
        "x_speed": int(ram[ADDR_ENEMY_X_SPEED + slot]),
        "y_speed": int(ram[ADDR_ENEMY_Y_SPEED + slot]),
        "x_move_force": int(ram[ADDR_ENEMY_X_MOVE_FORCE + slot]),
        "y_move_force": int(ram[ADDR_ENEMY_Y_MOVE_FORCE + slot]),
    }


def read_active_reward_target(
    core: MesenCore,
    *,
    player_x: int,
    behind_px: int = 192,
    ahead_px: int = 192,
) -> dict | None:
    """Read one coherent RAM snapshot and track the active reward target."""

    return decode_active_reward_target(
        read_nes_internal_ram(core),
        player_x=player_x,
        behind_px=behind_px,
        ahead_px=ahead_px,
    )
