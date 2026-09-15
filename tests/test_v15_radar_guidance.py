from pathlib import Path
import importlib.util
import sys


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
MODULE_PATH = EXAMPLES_DIR / "mesen_smb_checkpoint_planner_v15.py"
sys.path.insert(0, str(EXAMPLES_DIR))
try:
    spec = importlib.util.spec_from_file_location("planner_v15", MODULE_PATH)
    v15 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(v15)
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
    radar: dict,
):
    assert v15.v11._atomic_json(
        path,
        {
            "generation": generation,
            "worker": worker,
            "root_frame": root,
            "candidate": candidate,
            "schedule": [{"buttons": worker + 1, "frames": 8}],
            "score": [1, 0, progress, root + progress],
            "progress": progress,
            "terminal": "none",
            "surrogate_delta_x": float(progress),
            "risk_probability": risk,
            "no_progress_probability": 0.0,
            "compute_ms": 100.0 + worker,
            "radar": radar,
        },
    )


def test_v15_prefers_jump_rearm_when_enemy_is_near(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v15.v12.reset_response_cache()
    v15.v14._RISK_CUTOFF = 0.20
    radar = {
        "nearest_enemy_dx": 48,
        "nearest_gap_dx": None,
        "nearest_obstacle_dx": None,
    }

    _write(paths[0], generation=1, worker=0, root=100, candidate="run_8", progress=18, risk=0.05, radar=radar)
    _write(paths[1], generation=1, worker=1, root=100, candidate="rearm_jump_8", progress=13, risk=0.08, radar=radar)
    _write(paths[2], generation=1, worker=2, root=100, candidate="rearm_jump_12", progress=15, risk=0.09, radar=radar)
    _write(paths[3], generation=1, worker=3, root=100, candidate="right_8", progress=10, risk=0.03, radar=radar)

    plan = v15.best_coherent_radar_plan(paths, 108, 16, -1)

    assert plan is not None
    assert plan["candidate"] in {"rearm_jump_8", "rearm_jump_12"}
    assert plan["candidate"] == "rearm_jump_12"
    assert plan["guard_mode"].startswith("radar-jump[enemy:48]")


def test_v15_keeps_progress_choice_when_radar_is_clear(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v15.v12.reset_response_cache()
    v15.v14._RISK_CUTOFF = 0.20
    radar = {
        "nearest_enemy_dx": 150,
        "nearest_gap_dx": 140,
        "nearest_obstacle_dx": 120,
    }

    _write(paths[0], generation=2, worker=0, root=200, candidate="run_8", progress=18, risk=0.05, radar=radar)
    _write(paths[1], generation=2, worker=1, root=200, candidate="rearm_jump_8", progress=13, risk=0.08, radar=radar)
    _write(paths[2], generation=2, worker=2, root=200, candidate="rearm_jump_12", progress=15, risk=0.09, radar=radar)
    _write(paths[3], generation=2, worker=3, root=200, candidate="right_8", progress=10, risk=0.03, radar=radar)

    plan = v15.best_coherent_radar_plan(paths, 208, 16, -1)

    assert plan is not None
    assert plan["candidate"] == "run_8"
    assert plan["guard_mode"].startswith("radar-clear/")
