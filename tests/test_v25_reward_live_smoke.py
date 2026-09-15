from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v25():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v25.py"
        spec = spec_from_file_location("fami_pixel_v25_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(str(examples))
        except ValueError:
            pass


def test_v25_installs_reward_worker_selector_and_landing_radar_wrapper():
    v25 = _load_v25()
    assert len(v25.REWARD_BEAM_CHUNKS_WITH_HOLD) == 8
    assert v25._REWARD_PREFIX_FRAMES == 4

    v25.v24._install_v24_overrides()
    v25._install_v25_overrides()

    assert v25.v23.PLANNER_NAME == "v25-live-reward-intercept"
    assert v25.v23.shadow_worker_main is v25.shadow_worker_main
    assert v25.v23._best_forward_plan is v25._best_v25_plan
    assert v25.v20._tracking_landing_radar is v25._augmenting_landing_radar
    assert v25._REWARD_JUMP_NAMES.issubset(v25.v15._JUMP_NAMES)


def test_v25_collect_target_prefers_sticky_target_field():
    v25 = _load_v25()
    assert (
        v25._collect_target_from_radar(
            {"collect_target_type": "star", "nearest_reward_type": "mushroom"}
        )
        == "star"
    )
    assert v25._collect_target_from_radar({"nearest_reward_type": "fire_flower"}) == "fire_flower"
    assert v25._collect_target_from_radar({}) is None
