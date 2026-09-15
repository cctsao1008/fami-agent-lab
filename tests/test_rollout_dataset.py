from fami_pixel.games.smb1 import (
    ActionCommand,
    CandidateOutcome,
    CandidateTerminal,
    PlanCandidate,
    Smb1Action,
    Smb1Observation,
)
from fami_pixel.learning import build_rollout_record, summarize_rollout_records


def _observation(frame: int, x: int, y: int, vx: int, vy: int) -> Smb1Observation:
    return Smb1Observation(
        native_frame_id=frame,
        smb_frame_counter=frame & 0xFF,
        world=0,
        level=0,
        mario_x_abs=x,
        mario_y=y,
        mario_y_high=1,
        player_state=0,
        player_x_speed=vx & 0xFF,
        player_y_speed=vy & 0xFF,
        raw_joypad=0x82,
        oper_mode=1,
        oper_mode_task=3,
        game_engine_subroutine=0x08,
    )


def test_rollout_record_preserves_raw_state_and_signed_targets():
    candidate = PlanCandidate(
        "run_jump",
        (
            ActionCommand(Smb1Action.RIGHT_A_B, 8),
            ActionCommand(Smb1Action.RIGHT_B, 4),
        ),
    )
    start = _observation(100, 200, 176, 40, 0)
    end = _observation(112, 230, 160, 36, -3)
    outcome = CandidateOutcome(
        candidate=candidate,
        start_x=200,
        end_x=230,
        max_x=232,
        elapsed_frames=12,
        terminal=CandidateTerminal.NONE,
    )

    record = build_rollout_record(start, end, outcome, source="test", generation=7)

    assert record["schema"] == 1
    assert record["candidate"]["horizon_frames"] == 12
    assert record["start"]["vx"] == 40
    assert record["end"]["vy"] == -3
    assert record["target"]["delta_x"] == 30
    assert record["target"]["death"] is False
    assert record["target"]["no_progress"] is False
    assert record["target"]["doomed_within_probe"] is False
    assert "probe_terminal" not in record["target"]


def test_rollout_record_marks_delayed_death_from_authoritative_probe():
    candidate = PlanCandidate("coast", (ActionCommand(Smb1Action.NOOP, 8),))
    start = _observation(200, 400, 176, 18, 2)
    end = _observation(208, 410, 182, 12, 4)
    outcome = CandidateOutcome(
        candidate=candidate,
        start_x=400,
        end_x=410,
        max_x=412,
        elapsed_frames=8,
        terminal=CandidateTerminal.NONE,
    )

    record = build_rollout_record(
        start,
        end,
        outcome,
        source="probe-test",
        generation=3,
        probe_terminal="death",
        probe_frames=48,
    )

    assert record["target"]["death"] is False
    assert record["target"]["doomed_within_probe"] is True
    assert record["target"]["probe_terminal"] == "death"
    assert record["target"]["probe_frames"] == 48


def test_rollout_summary_counts_hazards_terminals_and_probe_labels():
    candidate = PlanCandidate("right", (ActionCommand(Smb1Action.RIGHT, 8),))
    safe = CandidateOutcome(candidate, 10, 20, 20, 8)
    doomed = CandidateOutcome(candidate, 20, 25, 25, 8)
    dead = CandidateOutcome(candidate, 25, 25, 25, 8, CandidateTerminal.DEATH)

    safe_record = build_rollout_record(
        _observation(1, 10, 176, 20, 0),
        _observation(9, 20, 176, 20, 0),
        safe,
        source="test",
    )
    doomed_record = build_rollout_record(
        _observation(10, 20, 176, 20, 0),
        _observation(18, 25, 190, 8, 4),
        doomed,
        source="test",
        probe_terminal="death",
        probe_frames=48,
    )
    dead_record = build_rollout_record(
        _observation(20, 25, 176, 20, 0),
        _observation(28, 25, 200, 0, 4),
        dead,
        source="test",
    )

    summary = summarize_rollout_records([safe_record, doomed_record, dead_record])

    assert summary["records"] == 3
    assert summary["death_records"] == 1
    assert summary["doomed_within_probe_records"] == 1
    assert summary["probed_records"] == 1
    assert summary["no_progress_records"] == 1
    assert summary["descending_low_records"] == 2
    assert summary["candidate_counts"] == {"right": 3}
