from dataclasses import dataclass

from fami_pixel.learning.hazard_sampling import select_depth_beam, select_hazard_branches


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


@dataclass(frozen=True)
class _BeamEntry:
    child_x: int
    outcome: _Outcome
    state_signature: tuple[int, int, int, int, int, int]


def _outcome(name: str, progress: int, *, terminal: str = "none", max_x: int | None = None):
    return _Outcome(
        candidate=_Candidate(name),
        progress=progress,
        max_x=progress if max_x is None else max_x,
        terminal=_Terminal(terminal),
    )


def _entry(name: str, child_x: int, progress: int, signature):
    return _BeamEntry(
        child_x=child_x,
        outcome=_outcome(name, progress, max_x=child_x),
        state_signature=tuple(signature),
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


def test_depth_beam_keeps_forward_leader_and_near_frontier_boundary():
    entries = [
        _entry("far_stall", 90, 0, (90, 180, 0, 0, 0, 8)),
        _entry("leader", 210, 30, (210, 160, 40, 0, 0, 8)),
        _entry("near_boundary", 185, 1, (185, 170, 15, -2, 1, 8)),
        _entry("near_diverse", 178, 12, (178, 140, -5, 4, 1, 8)),
    ]

    selected = select_depth_beam(entries, width=3, x_window=64)
    names = tuple(entry.outcome.candidate.name for entry in selected)

    assert names[0] == "leader"
    assert "near_boundary" in names
    assert "far_stall" not in names


def test_depth_beam_validates_parameters():
    entry = _entry("safe", 40, 2, (40, 180, 2, 0, 0, 8))

    for width, x_window, expected in ((0, 96, "width"), (1, -1, "x_window")):
        try:
            select_depth_beam([entry], width=width, x_window=x_window)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("expected ValueError")
