from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_reward_beam_probe_v2_installs_sticky_radar():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_reward_beam_probe_v2.py"
    name = "fami_pixel_reward_beam_probe_v2_smoke"
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        assert module.TRAILING_TARGET_PX == 192
        assert module.base._radar is module._sticky_reward_radar
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
