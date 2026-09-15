from fami_pixel.games.smb1.reward_motion import (
    relative_closing_score,
    reward_intercept_key_motion,
    signed_byte,
)


def test_signed_byte_decodes_native_smb_speed():
    assert signed_byte(0x10) == 16
    assert signed_byte(0xE8) == -24


def test_relative_motion_penalizes_fast_crossing_after_horizontal_alignment():
    # V3 replay shape: Star already to Mario's right and moving right (+16),
    # while Mario still carries strong leftward speed (-20). That is divergence,
    # not a good intercept merely because dx is small.
    diverging = reward_intercept_key_motion(
        reward={"type": "star", "dx": 5, "y": 122, "state": 0x80, "x_speed": 0x10, "y_speed": 0x01},
        nearest_enemy_dx=38,
        mario_y=145,
        player_x_speed=0xEC,  # -20
        player_y_speed=0xFF,  # -1
    )
    reversing = reward_intercept_key_motion(
        reward={"type": "star", "dx": 12, "y": 125, "state": 0x80, "x_speed": 0x10, "y_speed": 0x02},
        nearest_enemy_dx=38,
        mario_y=145,
        player_x_speed=0x18,  # +24, now capable of closing on a +16 Star
        player_y_speed=0x00,
    )
    assert reversing > diverging


def test_near_alignment_prefers_low_relative_speed_over_fast_crossing():
    stable = relative_closing_score(2, target_speed=16, player_speed=16)
    crossing = relative_closing_score(2, target_speed=16, player_speed=-20)
    assert stable > crossing
