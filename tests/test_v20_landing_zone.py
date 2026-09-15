from pathlib import Path
import importlib.util
import sys


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
MODULE_PATH = EXAMPLES_DIR / "mesen_smb_checkpoint_planner_v20.py"
sys.path.insert(0, str(EXAMPLES_DIR))
try:
    spec = importlib.util.spec_from_file_location("planner_v20", MODULE_PATH)
    v20 = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(v20)
finally:
    sys.path.remove(str(EXAMPLES_DIR))


def _radar(*dxs, grounded=True, invincible=False):
    return {
        "enemies": [
            {"slot": i, "id": 0x06, "state": 0, "x": 1000 + dx, "y": 176, "dx": dx}
            for i, dx in enumerate(dxs)
        ],
        "nearest_enemy_dx": min((dx for dx in dxs if dx >= 0), default=None),
        "nearest_gap_dx": None,
        "nearest_obstacle_dx": None,
        "grounded": grounded,
        "invincible": invincible,
    }


def test_grounded_unsafe_landing_corridor_forces_extended_rearm_jump():
    plan = v20.best_coherent_landing_plan(
        [],
        current_frame=1000,
        freshness=16,
        last_applied_generation=20,
        live_radar=_radar(42, 65, 126, 150, grounded=True),
    )

    assert plan is not None
    assert plan["candidate"] == "cluster_jump_16"
    assert plan["worker"] == "landing-reactive"
    assert "landing-zone-escape" in plan["guard_mode"]
    assert plan["schedule"][0]["frames"] == 1
    assert plan["schedule"][1]["frames"] == 15


def test_airborne_unsafe_landing_corridor_extends_current_jump():
    plan = v20.best_coherent_landing_plan(
        [],
        current_frame=1012,
        freshness=16,
        last_applied_generation=20,
        live_radar=_radar(42, 65, 126, 150, grounded=False),
    )

    assert plan is not None
    assert plan["candidate"] == "cluster_extend_8"
    assert "landing-zone-extend" in plan["guard_mode"]
    assert len(plan["schedule"]) == 1
    assert plan["schedule"][0]["frames"] == 8


def test_invincibility_does_not_treat_enemy_landing_corridor_as_fatal(monkeypatch):
    sentinel = {"candidate": "reward/base"}
    monkeypatch.setattr(v20, "_BASE_REWARD_PLAN", lambda *args, **kwargs: sentinel)

    plan = v20.best_coherent_landing_plan(
        [],
        current_frame=1020,
        freshness=16,
        last_applied_generation=20,
        live_radar=_radar(126, 150, grounded=True, invincible=True),
    )

    assert plan is sentinel


def test_safe_landing_corridor_preserves_v19_policy(monkeypatch):
    sentinel = {"candidate": "reward/base"}
    monkeypatch.setattr(v20, "_BASE_REWARD_PLAN", lambda *args, **kwargs: sentinel)

    plan = v20.best_coherent_landing_plan(
        [],
        current_frame=1030,
        freshness=16,
        last_applied_generation=20,
        live_radar=_radar(40, grounded=True),
    )

    assert plan is sentinel
