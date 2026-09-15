from fami_pixel.games.smb1.actions import Smb1Action
from fami_pixel.games.smb1.reward_beam import (
    REWARD_BEAM_CHUNKS,
    REWARD_BEAM_CHUNKS_WITH_HOLD,
    buttons_for_chunk_frame,
)


def test_extended_reward_vocabulary_preserves_historical_six_chunks():
    assert REWARD_BEAM_CHUNKS_WITH_HOLD[: len(REWARD_BEAM_CHUNKS)] == REWARD_BEAM_CHUNKS
    assert len(REWARD_BEAM_CHUNKS_WITH_HOLD) == len(REWARD_BEAM_CHUNKS) + 2


def test_jump_hold_chunks_keep_a_pressed_for_all_four_frames():
    for name, expected_action in (
        ("hold_right_jump4", Smb1Action.RIGHT_A_B),
        ("hold_left_jump4", Smb1Action.LEFT_A_B),
    ):
        chunk = next(item for item in REWARD_BEAM_CHUNKS_WITH_HOLD if item.name == name)
        assert chunk.frame_count == 4
        assert len(chunk.commands) == 1
        assert chunk.commands[0].action == expected_action
        assert all(
            buttons_for_chunk_frame(chunk, offset) == chunk.commands[0].nes_buttons
            for offset in range(4)
        )


def test_rearm_plus_hold_can_express_seven_continuous_a_frames_after_one_release_frame():
    rearm = next(item for item in REWARD_BEAM_CHUNKS_WITH_HOLD if item.name == "rearm_right_jump4")
    hold = next(item for item in REWARD_BEAM_CHUNKS_WITH_HOLD if item.name == "hold_right_jump4")
    schedule = [buttons_for_chunk_frame(rearm, i) for i in range(4)] + [
        buttons_for_chunk_frame(hold, i) for i in range(4)
    ]
    a_mask = 0x01
    assert schedule[0] & a_mask == 0
    assert all(button & a_mask for button in schedule[1:])
