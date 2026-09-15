from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def _load_v24():
    examples = (Path(__file__).resolve().parents[1] / "examples").resolve()
    sys.path.insert(0, str(examples))
    try:
        path = examples / "mesen_smb_checkpoint_planner_v24.py"
        spec = spec_from_file_location("fami_pixel_v24_smoke", path)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(str(examples))
        except ValueError:
            pass


def test_v24_installs_partial_selector_and_continuation_tail():
    v24 = _load_v24()
    v24._install_v24_overrides()
    assert v24.v23.PLANNER_NAME == "v24-forward-model-partial"
    assert v24.v23._best_forward_plan is v24._best_forward_plan_partial
    assert v24.v23.execution_prefix_schedule is v24.execution_prefix_with_continuation
