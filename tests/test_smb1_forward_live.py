from fami_pixel.games.smb1.forward_live import (
    execution_prefix_with_continuation,
    response_proves_execution_prefix,
    select_fresh_partial_safe_response,
)
from fami_pixel.games.smb1.forward_model import BASELINE_TRAJECTORY_PLANS


def _response(*, generation=10, root=100, event="landed", frames=24, score=(3, 0, 50, 50), safe=True):
    return {
        "generation": generation,
        "root_frame": root,
        "trajectory_event": event,
        "trajectory_frames": frames,
        "trajectory_safe_resolved": safe,
        "score": list(score),
    }


def test_short_preexisting_landing_does_not_prove_four_frame_prefix():
    assert not response_proves_execution_prefix(
        _response(frames=2),
        4,
    )
    assert response_proves_execution_prefix(
        _response(frames=24),
        4,
    )


def test_partial_safe_response_does_not_wait_for_slow_unknown_worker():
    responses = [
        _response(generation=20, root=200, frames=27, score=(3, 0, 68, 68)),
        {
            "generation": 19,
            "root_frame": 196,
            "trajectory_event": "horizon",
            "trajectory_frames": 64,
            "trajectory_safe_resolved": False,
            "score": [1, 0, 40, 40],
        },
    ]
    selected = select_fresh_partial_safe_response(
        responses,
        current_frame=208,
        freshness=16,
        last_applied_generation=18,
        prefix_frames=4,
    )
    assert selected is not None
    assert selected["generation"] == 20


def test_stale_safe_response_is_rejected():
    selected = select_fresh_partial_safe_response(
        [_response(generation=20, root=100, frames=27)],
        current_frame=140,
        freshness=16,
        last_applied_generation=18,
        prefix_frames=4,
    )
    assert selected is None


def test_jump_prefix_falls_back_to_right_b_not_release():
    long_jump = BASELINE_TRAJECTORY_PLANS[0]
    schedule = execution_prefix_with_continuation(long_jump, 4)
    assert sum(segment["frames"] for segment in schedule[:-1]) == 4
    assert schedule[-1]["frames"] == 1
    assert schedule[-1]["buttons"] == long_jump.commands[0].nes_buttons
