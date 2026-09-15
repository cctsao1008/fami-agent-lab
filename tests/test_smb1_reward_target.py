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
    decode_smb1_radar,
)
from fami_pixel.games.smb1.reward_target import decode_active_reward_target


def _star_ram(world_x: int) -> bytes:
    ram = bytearray(0x800)
    slot = POWER_UP_SLOT
    ram[ADDR_POWER_UP_TYPE] = 2
    ram[ADDR_ENEMY_FLAG + slot] = 1
    ram[ADDR_ENEMY_ID + slot] = POWER_UP_OBJECT_ID
    ram[ADDR_ENEMY_STATE + slot] = 3
    ram[ADDR_ENEMY_PAGE + slot] = (world_x >> 8) & 0xFF
    ram[ADDR_ENEMY_X + slot] = world_x & 0xFF
    ram[ADDR_ENEMY_Y_HIGH + slot] = 1
    ram[ADDR_ENEMY_Y + slot] = 144
    return bytes(ram)


def test_reward_target_tracker_keeps_star_farther_behind_than_forward_radar():
    ram = _star_ram(1616)
    player_x = 1640  # target is 24 px behind: outside radar's -16 trailing clip

    radar = decode_smb1_radar(ram, player_x=player_x, lookahead_px=192)
    tracked = decode_active_reward_target(ram, player_x=player_x, behind_px=192)

    assert radar.rewards == ()
    assert tracked is not None
    assert tracked["type"] == "star"
    assert tracked["dx"] == -24
    assert tracked["state"] == 3


def test_reward_target_tracker_respects_bounded_trailing_window():
    ram = _star_ram(1400)
    assert decode_active_reward_target(ram, player_x=1640, behind_px=192) is None


def test_reward_target_tracker_rejects_inactive_slot():
    ram = bytearray(_star_ram(1616))
    ram[ADDR_ENEMY_FLAG + POWER_UP_SLOT] = 0
    assert decode_active_reward_target(bytes(ram), player_x=1620) is None
