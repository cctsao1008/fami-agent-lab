from pathlib import Path
import importlib.util
import sys


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
MODULE_PATH = EXAMPLES_DIR / "mesen_smb_checkpoint_planner_v14.py"
sys.path.insert(0, str(EXAMPLES_DIR))
try:
    spec = importlib.util.spec_from_file_location("planner_v14", MODULE_PATH)
    v14 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(v14)
finally:
    sys.path.remove(str(EXAMPLES_DIR))


def _write(
    path: Path,
    *,
    generation: int,
    worker: int,
    root: int,
    candidate: str,
    progress: int,
    risk: float,
    stall: float = 0.0,
    terminal: str = "none",
):
    safe = -1 if terminal == "death" else 1
    assert v14.v11._atomic_json(
        path,
        {
            "generation": generation,
            "worker": worker,
            "root_frame": root,
            "candidate": candidate,
            "schedule": [{"buttons": worker + 1, "frames": 8}],
            "score": [safe, 0, progress, root + progress],
            "progress": progress,
            "terminal": terminal,
            "surrogate_delta_x": float(progress),
            "risk_probability": risk,
            "no_progress_probability": stall,
            "compute_ms": 100.0 + worker,
        },
    )


def test_v14_live_pool_releases_a_before_rearm_jump():
    candidates = {candidate.name: candidate for candidate in v14._candidate_pool()}
    assert set(candidates) == {"run_8", "rearm_jump_8", "rearm_jump_12", "right_8"}

    jump = candidates["rearm_jump_8"]
    assert len(jump.commands) == 2
    first, second = jump.commands
    assert int(first.frame_count) == 1
    assert not (int(first.nes_buttons) & int(v14.v11.NES_A))
    assert int(second.nes_buttons) & int(v14.v11.NES_A)
    assert int(second.nes_buttons) & int(v14.v11.NES_B)
    assert int(second.nes_buttons) & int(v14.v11.NES_RIGHT)


def test_v14_hard_cutoff_rejects_high_progress_risky_candidate(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v14.v12.reset_response_cache()
    old_cutoff = v14._RISK_CUTOFF
    v14._RISK_CUTOFF = 0.20
    try:
        _write(paths[0], generation=1, worker=0, root=100, candidate="run", progress=20, risk=0.55)
        _write(paths[1], generation=1, worker=1, root=100, candidate="jump8", progress=13, risk=0.10)
        _write(paths[2], generation=1, worker=2, root=100, candidate="jump12", progress=18, risk=0.35)
        _write(paths[3], generation=1, worker=3, root=100, candidate="right", progress=10, risk=0.08)

        plan = v14.best_coherent_hard_risk_plan(paths, 108, 16, -1)
        assert plan is not None
        assert plan["candidate"] in {"jump8", "right"}
        assert plan["risk_probability"] <= 0.20
        assert plan["guard_mode"] == "below-cutoff"
    finally:
        v14._RISK_CUTOFF = old_cutoff


def test_v14_falls_back_to_minimum_risk_when_all_candidates_are_risky(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v14.v12.reset_response_cache()
    old_cutoff = v14._RISK_CUTOFF
    v14._RISK_CUTOFF = 0.20
    try:
        _write(paths[0], generation=2, worker=0, root=200, candidate="a", progress=30, risk=0.80)
        _write(paths[1], generation=2, worker=1, root=200, candidate="b", progress=8, risk=0.41)
        _write(paths[2], generation=2, worker=2, root=200, candidate="c", progress=20, risk=0.55)
        _write(paths[3], generation=2, worker=3, root=200, candidate="d", progress=5, risk=0.62)

        plan = v14.best_coherent_hard_risk_plan(paths, 208, 16, -1)
        assert plan is not None
        assert plan["candidate"] == "b"
        assert plan["guard_mode"] == "min-risk-fallback"
    finally:
        v14._RISK_CUTOFF = old_cutoff
