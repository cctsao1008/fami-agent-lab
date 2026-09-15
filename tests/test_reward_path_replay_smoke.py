from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


def test_reward_path_replay_imports():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_reward_path_replay.py"
    module_name = "fami_pixel_reward_path_replay_smoke"
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

    assert module._DONE == "RewardPathReplay: DONE"
    assert "left4" in module._CHUNKS
    assert module._parse_path("left4,right4") == ("left4", "right4")
