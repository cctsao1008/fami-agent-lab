from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v23():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v23.py"
        spec = spec_from_file_location("fami_pixel_v23_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(str(examples))
        except ValueError:
            pass


def test_v23_imports_and_exposes_receding_horizon_contract():
    v23 = _load_v23()
    assert v23.LIVE_TRAJECTORY_HORIZON == 64
    assert v23.EXECUTION_PREFIX_FRAMES == 4
    assert "fm_long_jump" in v23._FORWARD_JUMP_NAMES
    assert len(v23._worker_plans(0, 6)) == 1
