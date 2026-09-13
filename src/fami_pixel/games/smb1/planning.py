"""Planning contracts for bounded SMB1 checkpoint search.

The planner layer ranks predicted/rolled-out alternatives but never becomes the
source of machine truth. A caller must evaluate candidates against Mesen (or a
future explicitly-marked predictive model) and feed the resulting outcomes
back into these pure ranking helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .actions import ActionCommand


class CandidateTerminal(str, Enum):
    NONE = "none"
    DEATH = "death"
    LEVEL_COMPLETE = "level_complete"


@dataclass(frozen=True)
class PlanCandidate:
    """A fixed-horizon action macro considered from one checkpoint."""

    name: str
    commands: tuple[ActionCommand, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("candidate name must not be empty")
        if not self.commands:
            raise ValueError("candidate must contain at least one command")

    @property
    def frame_count(self) -> int:
        return sum(command.frame_count for command in self.commands)


@dataclass(frozen=True)
class CandidateOutcome:
    """Auditable result of evaluating one candidate from one checkpoint."""

    candidate: PlanCandidate
    start_x: int
    end_x: int
    max_x: int
    elapsed_frames: int
    terminal: CandidateTerminal = CandidateTerminal.NONE
    reached_flagpole: bool = False

    def __post_init__(self) -> None:
        if self.elapsed_frames <= 0:
            raise ValueError("elapsed_frames must be > 0")
        if self.max_x < self.start_x:
            raise ValueError("max_x must be >= start_x")

    @property
    def progress(self) -> int:
        return self.end_x - self.start_x


def score_candidate(outcome: CandidateOutcome) -> float:
    """Rank a real rollout using a small, explicit M1 heuristic.

    Terminal semantics dominate progress. A flagpole entry is strongly favored
    because it is the machine-observed precursor to PlayerEndLevel. Otherwise
    the score is forward progress normalized by the actual rollout duration.
    """

    if outcome.terminal == CandidateTerminal.LEVEL_COMPLETE:
        return 1_000_000.0
    if outcome.terminal == CandidateTerminal.DEATH:
        return -1_000_000.0
    if outcome.reached_flagpole:
        return 100_000.0 + outcome.progress
    return outcome.progress / outcome.elapsed_frames


def select_best_candidate(outcomes: tuple[CandidateOutcome, ...]) -> CandidateOutcome:
    """Return the highest-scoring candidate with deterministic tie-breaking."""

    if not outcomes:
        raise ValueError("at least one candidate outcome is required")

    # Keep caller order as the final tie-breaker by using max() over indices
    # only on score/progress/max_x. Python's max returns the first equal item.
    return max(
        outcomes,
        key=lambda outcome: (
            score_candidate(outcome),
            outcome.progress,
            outcome.max_x,
        ),
    )
