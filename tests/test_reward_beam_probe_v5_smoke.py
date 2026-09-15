from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_reward_beam_probe_v5_imports_with_extended_vocabulary():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_reward_beam_probe_v5.py"
    module_name = "fami_pixel_reward_beam_probe_v5_smoke"
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

    import smb1_reward_beam_probe as base

    names = {chunk.name for chunk in base.REWARD_BEAM_CHUNKS}
    assert "hold_right_jump4" in names
    assert "hold_left_jump4" in names
    assert len(base.REWARD_BEAM_CHUNKS) >= 8
