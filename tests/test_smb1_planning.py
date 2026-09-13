import pytest

from fami_pixel.games.smb1.actions import ActionCommand, Smb1Action
from fami_pixel.games.smb1.planning import (
    CandidateOutcome,
    CandidateTerminal,
    PlanCandidate,
    score_candidate,
    select_best_candidate,
)


def _candidate(name: str, action: Smb1Action, frames: int = 30) -> PlanCandidate:
    return PlanCandidate(name, (ActionCommand(action, frames),))


def test_plan_candidate_reports_total_horizon() -> None:
    candidate = PlanCandidate(
        "jump_then_run",
        (
            ActionCommand(Smb1Action.RIGHT_A, 12),
            ActionCommand(Smb1Action.RIGHT, 18),
        ),
    )
    assert candidate.frame_count == 30


def test_candidate_requires_commands() -> None:
    with pytest.raises(ValueError):
        PlanCandidate("empty", ())


def test_death_is_ranked_below_safe_progress() -> None:
    safe = CandidateOutcome(
        candidate=_candidate("safe", Smb1Action.RIGHT),
        start_x=100,
        end_x=130,
        max_x=130,
        elapsed_frames=30,
    )
    death = CandidateOutcome(
        candidate=_candidate("death", Smb1Action.RIGHT_A),
        start_x=100,
        end_x=160,
        max_x=165,
        elapsed_frames=25,
        terminal=CandidateTerminal.DEATH,
    )

    assert score_candidate(death) < score_candidate(safe)
    assert select_best_candidate((death, safe)) is safe


def test_flagpole_is_ranked_above_ordinary_progress() -> None:
    ordinary = CandidateOutcome(
        candidate=_candidate("ordinary", Smb1Action.RIGHT),
        start_x=3000,
        end_x=3060,
        max_x=3060,
        elapsed_frames=30,
    )
    flagpole = CandidateOutcome(
        candidate=_candidate("flagpole", Smb1Action.RIGHT_A),
        start_x=3000,
        end_x=3020,
        max_x=3020,
        elapsed_frames=30,
        reached_flagpole=True,
    )

    assert select_best_candidate((ordinary, flagpole)) is flagpole


def test_level_complete_is_ranked_highest() -> None:
    complete = CandidateOutcome(
        candidate=_candidate("complete", Smb1Action.RIGHT),
        start_x=3100,
        end_x=3120,
        max_x=3120,
        elapsed_frames=20,
        terminal=CandidateTerminal.LEVEL_COMPLETE,
    )
    flagpole = CandidateOutcome(
        candidate=_candidate("flagpole", Smb1Action.RIGHT_A),
        start_x=3100,
        end_x=3130,
        max_x=3130,
        elapsed_frames=30,
        reached_flagpole=True,
    )

    assert select_best_candidate((flagpole, complete)) is complete
