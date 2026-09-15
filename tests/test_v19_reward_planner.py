from pathlib import Path
import importlib.util
import sys


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
MODULE_PATH = EXAMPLES_DIR / "mesen_smb_checkpoint_planner_v19.py"
sys.path.insert(0, str(EXAMPLES_DIR))
try:
    spec = importlib.util.spec_from_file_location("planner_v19", MODULE_PATH)
    v19 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(v19)
finally:
    sys.path.remove(str(EXAMPLES_DIR))


def _write(
    path: Path,
    *,
    worker: int,
    candidate: str,
    progress: int,
    risk: float,
    terminal: str = "none",
):
    assert v19.v11._atomic_json(
        path,
        {
            "generation": 7,
            "worker": worker,
            "root_frame": 200,
            "candidate": candidate,
            "schedule": [{"buttons": worker + 1, "frames": 8}],
            "score": [-1 if terminal == "death" else 1, 0, progress, 200 + progress],
            "progress": progress,
            "terminal": terminal,
            "surrogate_delta_x": float(progress),
            "risk_probability": risk,
            "no_progress_probability": 0.0,
            "compute_ms": 100.0,
        },
    )


def test_star_invincibility_suppresses_enemy_hazard_but_not_gap():
    radar = {
        "nearest_enemy_dx": 32,
        "nearest_gap_dx": None,
        "nearest_obstacle_dx": None,
        "invincible": True,
    }
    assert v19._reward_aware_radar_reason(radar) is None

    radar["nearest_gap_dx"] = 40
    assert v19._reward_aware_radar_reason(radar) == "gap:40"


def test_reward_aware_plan_prefers_safe_star_jump(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v19.v12.reset_response_cache()
    v19.v14._RISK_CUTOFF = 0.20

    _write(paths[0], worker=0, candidate="run_8", progress=16, risk=0.03)
    _write(paths[1], worker=1, candidate="rearm_jump_8", progress=10, risk=0.04)
    _write(paths[2], worker=2, candidate="rearm_jump_12", progress=13, risk=0.05)
    _write(paths[3], worker=3, candidate="right_8", progress=8, risk=0.01)

    radar = {
        "lookahead_px": 192,
        "nearest_enemy_dx": None,
        "nearest_gap_dx": None,
        "nearest_obstacle_dx": None,
        "nearest_reward_dx": 56,
        "nearest_reward_type": "star",
        "player_status": 0,
        "star_invincible_timer": 0,
        "invincible": False,
        "rewards": [
            {"slot": 5, "type": "star", "power_up_type": 2, "dx": 56, "y": 144},
        ],
    }

    plan = v19.best_coherent_reward_plan(
        paths,
        current_frame=208,
        freshness=16,
        last_applied_generation=-1,
        live_radar=radar,
    )

    assert plan is not None
    assert plan["candidate"] == "rearm_jump_12"
    assert plan["pursuit_mode"] == "pursuit"
    assert plan["reward_target_type"] == "star"
    assert plan["reward_target_dx"] == 56
    assert plan["guard_mode"].startswith("reward-pursuit[star:56")


def test_current_hazard_stays_higher_priority_than_reward(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v19.v12.reset_response_cache()
    v19.v14._RISK_CUTOFF = 0.20

    _write(paths[0], worker=0, candidate="run_8", progress=16, risk=0.03)
    _write(paths[1], worker=1, candidate="rearm_jump_8", progress=10, risk=0.70)
    _write(paths[2], worker=2, candidate="rearm_jump_12", progress=13, risk=0.80)
    _write(paths[3], worker=3, candidate="right_8", progress=8, risk=0.01)

    radar = {
        "lookahead_px": 192,
        "nearest_enemy_dx": 48,
        "nearest_gap_dx": None,
        "nearest_obstacle_dx": None,
        "nearest_reward_dx": 40,
        "nearest_reward_type": "star",
        "player_status": 0,
        "star_invincible_timer": 0,
        "invincible": False,
        "rewards": [
            {"slot": 5, "type": "star", "power_up_type": 2, "dx": 40, "y": 144},
        ],
    }

    # Ensure the captured V16 planner observes V19's reward-aware hazard reason.
    old_reason = v19.v15._radar_reason
    v19.v15._radar_reason = v19._reward_aware_radar_reason
    try:
        plan = v19.best_coherent_reward_plan(
            paths,
            current_frame=208,
            freshness=16,
            last_applied_generation=-1,
            live_radar=radar,
        )
    finally:
        v19.v15._radar_reason = old_reason

    assert plan is not None
    assert plan["candidate"] == "rearm_jump_12"
    assert plan["pursuit_mode"] == "hazard-first"
    assert plan["guard_mode"] == "live-radar-jump[enemy:48]"
