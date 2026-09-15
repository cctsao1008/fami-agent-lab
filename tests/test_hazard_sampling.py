from dataclasses import dataclass

from fami_pixel.learning.hazard_sampling import select_hazard_branches


@dataclass(frozen=True)
class _Candidate:
    name: str


@dataclass(frozen=True)
class _Terminal:
    value: str


@dataclass(frozen=True)
class _Outcome:
    candidate: _Candidate
    progress: int
    max_x: int
    terminal: _Terminal


def _outcome(name: str, progress: int, *, terminal: str = "none", max_x: int | None = None):
    return _Outcome(
        candidate=_Candidate(name),
        progress=progress,
        max_x=progress if max_x is None else max_x,
        terminal=_Terminal(terminal),
    )


def test_hazard_branch_selection_mixes_low_boundary_and_best_survivors():
    outcomes = [
        _outcome("dead", 20, terminal="death"),
        _outcome("regress", -4),
        _outcome("near_zero", 1),
        _outcome("middle", 12),
        _outcome("best", 30),
    ]

    selected = select_hazard_branches(outcomes, width=3)

    assert tuple(outcome.candidate.name for outcome in selected) == (
        "regress",
        "near_zero",
        "best",
    )


def test_hazard_branch_selection_never_promotes_terminal_children():
    outcomes = [
        _outcome("death_a", 0, terminal="death"),
        _outcome("death_b", 5, terminal="death"),
    ]

    assert select_hazard_branches(outcomes, width=3) == ()


def test_hazard_branch_selection_validates_width():
    try:
        select_hazard_branches([_outcome("safe", 1)], width=0)
    except ValueError as exc:
        assert "width" in str(exc)
    else:
        raise AssertionError("expected ValueError")
