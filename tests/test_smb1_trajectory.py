from fami_pixel.games.smb1 import ActionCommand, Smb1Action
from fami_pixel.games.smb1.trajectory import (
    TrajectoryEvent,
    TrajectoryPlan,
    TrajectoryResult,
    _buttons_for_frame,
    _capability_changed,
    _target_reward_collected,
    trajectory_outcome_key,
)


def _result(event: TrajectoryEvent, **overrides) -> TrajectoryResult:
    values = dict(
        plan=TrajectoryPlan("probe", (ActionCommand(Smb1Action.RIGHT_B, 2),)),
        event=event,
        frames_simulated=8,
        start_frame=100,
        end_frame=108,
        start_x=100,
        end_x=112,
        max_x=114,
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
    values.update(overrides)
    return TrajectoryResult(**values)


def test_trajectory_plan_uses_prefix_then_tail_action():
    plan = TrajectoryPlan(
        "brake-jump",
        (
            ActionCommand(Smb1Action.LEFT, 2),
            ActionCommand(Smb1Action.RIGHT_A_B, 3),
        ),
        tail_action=Smb1Action.RIGHT_B,
    )

    assert _buttons_for_frame(plan, 0) == ActionCommand(Smb1Action.LEFT, 1).nes_buttons
    assert _buttons_for_frame(plan, 1) == ActionCommand(Smb1Action.LEFT, 1).nes_buttons
    assert _buttons_for_frame(plan, 2) == ActionCommand(Smb1Action.RIGHT_A_B, 1).nes_buttons
    assert _buttons_for_frame(plan, 4) == ActionCommand(Smb1Action.RIGHT_A_B, 1).nes_buttons
    assert _buttons_for_frame(plan, 5) == ActionCommand(Smb1Action.RIGHT_B, 1).nes_buttons


def test_reward_collection_requires_native_capability_evidence():
    start = {"player_status": 0, "star_invincible_timer": 0}

    assert _target_reward_collected("mushroom", start, {"player_status": 1})
    assert _target_reward_collected("fire_flower", start, {"player_status": 2})
    assert _target_reward_collected("star", start, {"star_invincible_timer": 24})

    # Object disappearance alone is not enough, and 1-Up is intentionally not
    # claimed until an authoritative lives counter is part of the native state.
    assert not _target_reward_collected("star", start, {"star_invincible_timer": 0})
    assert not _target_reward_collected("one_up", start, {"player_status": 0})


def test_capability_change_detects_status_or_star_timer():
    start = {"player_status": 0, "star_invincible_timer": 0}
    assert _capability_changed(start, {"player_status": 1, "star_invincible_timer": 0})
    assert _capability_changed(start, {"player_status": 0, "star_invincible_timer": 8})
    assert not _capability_changed(start, {"player_status": 0, "star_invincible_timer": 0})


def test_authoritative_outcome_class_dominates_progress():
    death = _result(TrajectoryEvent.DEATH, end_x=300, max_x=320)
    landed = _result(TrajectoryEvent.LANDED, end_x=130, max_x=132)
    reward = _result(
        TrajectoryEvent.REWARD_COLLECTED,
        end_x=105,
        max_x=108,
        target_reward_type="star",
        target_dx_start=40,
        target_dx_end=None,
    )
    win = _result(TrajectoryEvent.WIN, end_x=101, max_x=101)

    assert trajectory_outcome_key(landed) > trajectory_outcome_key(death)
    assert trajectory_outcome_key(reward) > trajectory_outcome_key(landed)
    assert trajectory_outcome_key(win) > trajectory_outcome_key(reward)


def test_target_approach_breaks_ties_inside_same_outcome_class():
    closer = _result(
        TrajectoryEvent.HORIZON,
        target_reward_type="star",
        target_dx_start=60,
        target_dx_end=20,
    )
    farther = _result(
        TrajectoryEvent.HORIZON,
        target_reward_type="star",
        target_dx_start=60,
        target_dx_end=50,
        end_x=140,
        max_x=150,
    )

    assert trajectory_outcome_key(closer) > trajectory_outcome_key(farther)
