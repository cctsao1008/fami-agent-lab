from pathlib import Path
import importlib.util
from types import SimpleNamespace
import sys


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
MODULE_PATH = EXAMPLES_DIR / "mesen_smb_checkpoint_planner_v16.py"
sys.path.insert(0, str(EXAMPLES_DIR))
try:
    spec = importlib.util.spec_from_file_location("planner_v16", MODULE_PATH)
    v16 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(v16)
finally:
    sys.path.remove(str(EXAMPLES_DIR))


def _write(path: Path, *, worker: int, candidate: str, progress: int, risk: float):
    assert v16.v11._atomic_json(
        path,
        {
            "generation": 5,
            "worker": worker,
            "root_frame": 100,
            "candidate": candidate,
            "schedule": [{"buttons": worker + 1, "frames": 8}],
            "score": [1, 0, progress, 100 + progress],
            "progress": progress,
            "terminal": "none",
            "surrogate_delta_x": float(progress),
            "risk_probability": risk,
            "no_progress_probability": 0.0,
            "compute_ms": 100.0,
        },
    )


def test_live_radar_enemy_overrides_risk_gate_that_would_delete_jump(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v16.v12.reset_response_cache()
    v16.v14._RISK_CUTOFF = 0.20

    _write(paths[0], worker=0, candidate="run_8", progress=12, risk=0.05)
    _write(paths[1], worker=1, candidate="rearm_jump_8", progress=10, risk=0.70)
    _write(paths[2], worker=2, candidate="rearm_jump_12", progress=14, risk=0.80)
    _write(paths[3], worker=3, candidate="right_8", progress=8, risk=0.03)

    plan = v16.best_coherent_live_radar_plan(
        paths,
        current_frame=108,
        freshness=16,
        last_applied_generation=-1,
        live_radar={"nearest_enemy_dx": 64, "nearest_gap_dx": None, "nearest_obstacle_dx": None},
    )

    assert plan is not None
    assert plan["candidate"] == "rearm_jump_12"
    assert plan["guard_mode"] == "live-radar-jump[enemy:64]"


def test_live_radar_clear_keeps_normal_risk_gate(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v16.v12.reset_response_cache()
    v16.v14._RISK_CUTOFF = 0.20

    _write(paths[0], worker=0, candidate="run_8", progress=12, risk=0.05)
    _write(paths[1], worker=1, candidate="rearm_jump_8", progress=14, risk=0.70)
    _write(paths[2], worker=2, candidate="rearm_jump_12", progress=16, risk=0.80)
    _write(paths[3], worker=3, candidate="right_8", progress=8, risk=0.03)

    plan = v16.best_coherent_live_radar_plan(
        paths,
        current_frame=108,
        freshness=16,
        last_applied_generation=-1,
        live_radar={"nearest_enemy_dx": 140, "nearest_gap_dx": None, "nearest_obstacle_dx": None},
    )

    assert plan is not None
    assert plan["candidate"] == "run_8"
    assert plan["guard_mode"] == "live-radar-clear/below-cutoff"


def test_grounded_detection_requires_low_zero_vertical_speed():
    grounded = SimpleNamespace(mario_y_high=1, mario_y=176, player_y_speed=0)
    rising = SimpleNamespace(mario_y_high=1, mario_y=160, player_y_speed=0xFC)
    airborne = SimpleNamespace(mario_y_high=1, mario_y=120, player_y_speed=0)

    assert v16._looks_grounded(grounded)
    assert not v16._looks_grounded(rising)
    assert not v16._looks_grounded(airborne)
