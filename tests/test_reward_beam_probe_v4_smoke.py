from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_reward_beam_probe_v4_imports():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_reward_beam_probe_v4.py"
    module_name = "fami_pixel_reward_beam_probe_v4_smoke"
    spec = spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous

    assert module.base.__file__ == str(path)
    assert callable(module._simulate_chunk_motion)
    assert callable(module._node_from_outcome_motion)
