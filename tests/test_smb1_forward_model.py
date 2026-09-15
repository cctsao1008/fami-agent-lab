from fami_pixel.games.smb1.forward_model import (
    BASELINE_TRAJECTORY_PLANS,
    execution_prefix_schedule,
    result_is_safe_resolved,
    select_safe_resolved_result,
    shard_trajectory_plans,
)
from fami_pixel.games.smb1.trajectory import TrajectoryEvent, TrajectoryResult


def _result(event: TrajectoryEvent) -> TrajectoryResult:
    plan = BASELINE_TRAJECTORY_PLANS[0]
    return TrajectoryResult(
        plan=plan,
        event=event,
        frames_simulated=8,
        start_frame=1,
        end_frame=9,
        start_x=100,
        end_x=120,
        max_x=120,
        start_y=176,
        end_y=176,
        airborne_seen=True,
        landed=event == TrajectoryEvent.LANDED,
        died=event == TrajectoryEvent.DEATH,
        won=event == TrajectoryEvent.WIN,
        capability_changed=event == TrajectoryEvent.CAPABILITY_CHANGED,
        reward_collected=event == TrajectoryEvent.REWARD_COLLECTED,
        target_reward_type=None,
        target_dx_start=None,
        target_dx_end=None,
        player_status_start=0,
        player_status_end=0,
        star_timer_start=0,
        star_timer_end=0,
    )


def test_six_workers_get_one_baseline_plan_each():
    shards = [shard_trajectory_plans(index, 6) for index in range(6)]
    assert all(len(shard) == 1 for shard in shards)
    assert {shard[0].name for shard in shards} == {
        plan.name for plan in BASELINE_TRAJECTORY_PLANS
    }


def test_execution_prefix_is_receding_and_release_tailed():
    long_jump = BASELINE_TRAJECTORY_PLANS[0]
    schedule = execution_prefix_schedule(long_jump, 4)
    assert schedule[0]["frames"] == 1
    assert schedule[1]["frames"] == 3
    assert schedule[-1] == {"buttons": 0x00, "frames": 1}
    assert sum(segment["frames"] for segment in schedule[:-1]) == 4


def test_horizon_is_not_safe_but_landing_is():
    assert not result_is_safe_resolved(_result(TrajectoryEvent.HORIZON))
    assert not result_is_safe_resolved(_result(TrajectoryEvent.DEATH))
    assert result_is_safe_resolved(_result(TrajectoryEvent.LANDED))


def test_safe_selector_rejects_death_and_horizon_only_sets():
    assert select_safe_resolved_result(
        [_result(TrajectoryEvent.DEATH), _result(TrajectoryEvent.DEATH)]
    ) is None
    assert select_safe_resolved_result(
        [_result(TrajectoryEvent.DEATH), _result(TrajectoryEvent.HORIZON)]
    ) is None


def test_safe_selector_prefers_resolved_safe_event_over_death():
    landing = _result(TrajectoryEvent.LANDED)
    selected = select_safe_resolved_result(
        [_result(TrajectoryEvent.DEATH), landing, _result(TrajectoryEvent.HORIZON)]
    )
    assert selected is landing
