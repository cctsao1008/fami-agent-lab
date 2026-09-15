from pathlib import Path
import importlib.util
import sys


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
MODULE_PATH = EXAMPLES_DIR / "mesen_smb_checkpoint_planner_v13.py"
sys.path.insert(0, str(EXAMPLES_DIR))
try:
    spec = importlib.util.spec_from_file_location("planner_v13", MODULE_PATH)
    v13 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(v13)
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
    predicted_dx: float | None = None,
):
    safe = -1 if terminal == "death" else 1
    assert v13.v11._atomic_json(
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
            "surrogate_delta_x": float(progress if predicted_dx is None else predicted_dx),
            "risk_probability": risk,
            "no_progress_probability": stall,
            "compute_ms": 100.0 + worker,
        },
    )


def test_v13_prefers_lower_delayed_risk_over_small_progress_gain(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v13.v12.reset_response_cache()

    _write(paths[0], generation=1, worker=0, root=100, candidate="run", progress=12, risk=0.08)
    _write(paths[1], generation=1, worker=1, root=100, candidate="jump8", progress=13, risk=0.12)
    _write(paths[2], generation=1, worker=2, root=100, candidate="jump12", progress=18, risk=0.95)
    _write(paths[3], generation=1, worker=3, root=100, candidate="right", progress=10, risk=0.03)

    plan = v13.best_coherent_risk_guarded_plan(paths, 108, 16, -1)

    assert plan is not None
    assert plan["candidate"] == "jump8"
    assert plan["cohort_size"] == 4
    assert plan["risk_probability"] == 0.12


def test_v13_never_prefers_immediate_mesen_death(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v13.v12.reset_response_cache()

    _write(
        paths[0], generation=2, worker=0, root=200, candidate="dead_fast", progress=50,
        risk=0.0, terminal="death",
    )
    _write(paths[1], generation=2, worker=1, root=200, candidate="safe", progress=4, risk=0.2)
    _write(paths[2], generation=2, worker=2, root=200, candidate="safe2", progress=3, risk=0.3)
    _write(paths[3], generation=2, worker=3, root=200, candidate="safe3", progress=2, risk=0.4)

    plan = v13.best_coherent_risk_guarded_plan(paths, 208, 16, -1)

    assert plan is not None
    assert plan["candidate"] != "dead_fast"
    assert plan["terminal"] == "none"
