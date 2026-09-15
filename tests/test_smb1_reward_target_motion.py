from fami_pixel.games.smb1.radar import (
    ADDR_ENEMY_FLAG,
    ADDR_ENEMY_ID,
    ADDR_ENEMY_PAGE,
    ADDR_ENEMY_STATE,
    ADDR_ENEMY_X,
    ADDR_ENEMY_Y,
    ADDR_ENEMY_Y_HIGH,
    ADDR_POWER_UP_TYPE,
    POWER_UP_OBJECT_ID,
    POWER_UP_SLOT,
)
from fami_pixel.games.smb1.reward_target import (
    ADDR_ENEMY_X_MOVE_FORCE,
    ADDR_ENEMY_X_SPEED,
    ADDR_ENEMY_Y_MOVE_FORCE,
    ADDR_ENEMY_Y_SPEED,
    decode_active_reward_target,
)


def test_reward_target_exposes_native_motion_bytes():
    ram = bytearray(0x800)
    slot = POWER_UP_SLOT
    ram[ADDR_POWER_UP_TYPE] = 2
    ram[ADDR_ENEMY_FLAG + slot] = 1
    ram[ADDR_ENEMY_ID + slot] = POWER_UP_OBJECT_ID
    ram[ADDR_ENEMY_STATE + slot] = 0x80
    ram[ADDR_ENEMY_PAGE + slot] = 0x06
    ram[ADDR_ENEMY_X + slot] = 0x40
    ram[ADDR_ENEMY_Y_HIGH + slot] = 1
    ram[ADDR_ENEMY_Y + slot] = 0x78
    ram[ADDR_ENEMY_X_SPEED + slot] = 0x10
    ram[ADDR_ENEMY_Y_SPEED + slot] = 0xFE
    ram[ADDR_ENEMY_X_MOVE_FORCE + slot] = 0x55
    ram[ADDR_ENEMY_Y_MOVE_FORCE + slot] = 0xAA

    target = decode_active_reward_target(bytes(ram), player_x=0x0620)

    assert target is not None
    assert target["x_speed"] == 0x10
    assert target["y_speed"] == 0xFE
    assert target["x_move_force"] == 0x55
    assert target["y_move_force"] == 0xAA
