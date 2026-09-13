import pytest

from fami_pixel.games.smb1 import (
    EpisodeAccumulator,
    EpisodeTermination,
    GameEvent,
    GameEventType,
    Smb1Observation,
)


def _obs(frame: int, x: int, state: int = 0) -> Smb1Observation:
    return Smb1Observation(
        native_frame_id=frame,
        smb_frame_counter=frame & 0xFF,
        world=0,
        level=0,
        mario_x_abs=x,
        mario_y=0xB0,
        mario_y_high=1,
        player_state=state,
        player_x_speed=0,
        player_y_speed=0,
        raw_joypad=0,
        oper_mode=1,
        oper_mode_task=3,
        game_engine_subroutine=0x08,
    )


def test_episode_result_tracks_progress_and_event_counts() -> None:
    acc = EpisodeAccumulator(_obs(100, 40))
    acc.record(
        _obs(101, 41, 1),
        (
            GameEvent(GameEventType.MOVED, 101, delta_x=1),
            GameEvent(GameEventType.JUMP_STARTED, 101),
        ),
    )
    acc.record(
        _obs(120, 70, 0),
        (
            GameEvent(GameEventType.MOVED, 120, delta_x=29),
            GameEvent(GameEventType.LANDED, 120),
        ),
    )

    result = acc.finish(EpisodeTermination.LEVEL_COMPLETE)

    assert result.termination == EpisodeTermination.LEVEL_COMPLETE
    assert result.elapsed_frames == 20
    assert result.start_x == 40
    assert result.end_x == 70
    assert result.max_x == 70
    assert result.net_progress == 30
    assert result.event_count == 4
    assert result.moved_events == 2
    assert result.jump_events == 1
    assert result.landing_events == 1


def test_episode_result_preserves_max_progress_after_backtrack() -> None:
    acc = EpisodeAccumulator(_obs(10, 40))
    acc.record(_obs(11, 80), ())
    acc.record(_obs(12, 70), ())

    result = acc.finish(EpisodeTermination.DEATH)

    assert result.end_x == 70
    assert result.max_x == 80
    assert result.net_progress == 30


def test_episode_accumulator_rejects_nonmonotonic_or_post_finish_updates() -> None:
    acc = EpisodeAccumulator(_obs(10, 40))
    with pytest.raises(ValueError):
        acc.record(_obs(10, 41), ())

    acc.finish(EpisodeTermination.TIMEOUT)
    with pytest.raises(RuntimeError):
        acc.record(_obs(11, 41), ())
    with pytest.raises(RuntimeError):
        acc.finish(EpisodeTermination.EXPLICIT_RESET)
