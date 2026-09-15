from fami_pixel.games.smb1.reward_beam import (
    REWARD_BEAM_CHUNKS,
    buttons_for_chunk_frame,
    matching_reward,
    reward_beam_key,
    reward_collection_proven,
    reward_object_is_active,
)


def test_reward_beam_chunks_are_four_frames():
    assert REWARD_BEAM_CHUNKS
    assert all(chunk.frame_count == 4 for chunk in REWARD_BEAM_CHUNKS)


def test_matching_reward_prefers_nearest_requested_type():
    radar = {
        "rewards": [
            {"type": "star", "dx": -12, "state": 3},
            {"type": "mushroom", "dx": 2, "state": 3},
            {"type": "star", "dx": -1, "state": 2},
        ]
    }
    assert matching_reward(radar, "star")["dx"] == -1


def test_star_collection_requires_timer_increase():
    assert not reward_collection_proven(
        "star",
        baseline_player_status=0,
        baseline_star_timer=0,
        radar={"player_status": 0, "star_invincible_timer": 0},
    )
    assert reward_collection_proven(
        "star",
        baseline_player_status=0,
        baseline_star_timer=0,
        radar={"player_status": 0, "star_invincible_timer": 120},
    )


def test_reward_object_active_state_uses_native_bit7_transition():
    assert not reward_object_is_active({"state": 2})
    assert not reward_object_is_active({"state": 17})
    assert reward_object_is_active({"state": 0x80})
    assert reward_object_is_active({"state": 0x81})


def test_reward_beam_prefers_active_target_over_still_emerging_target():
    active = reward_beam_key(
        reward={"type": "star", "dx": -8, "state": 0x80},
        nearest_enemy_dx=50,
        mario_x=1600,
    )
    emerging = reward_beam_key(
        reward={"type": "star", "dx": 0, "state": 17},
        nearest_enemy_dx=100,
        mario_x=1600,
    )
    assert active > emerging


def test_reward_beam_prefers_near_target_over_progress_within_same_phase():
    near = reward_beam_key(
        reward={"type": "star", "dx": -2, "state": 0x80},
        nearest_enemy_dx=80,
        mario_x=1600,
    )
    far = reward_beam_key(
        reward={"type": "star", "dx": 40, "state": 0x80},
        nearest_enemy_dx=120,
        mario_x=1700,
    )
    missing = reward_beam_key(
        reward=None,
        nearest_enemy_dx=200,
        mario_x=1800,
    )
    assert near > far > missing


def test_buttons_for_chunk_frame_covers_exact_schedule():
    left_jump = next(chunk for chunk in REWARD_BEAM_CHUNKS if chunk.name == "rearm_left_jump4")
    assert buttons_for_chunk_frame(left_jump, 0) == left_jump.commands[0].nes_buttons
    assert buttons_for_chunk_frame(left_jump, 1) == left_jump.commands[1].nes_buttons
    assert buttons_for_chunk_frame(left_jump, 3) == left_jump.commands[1].nes_buttons
