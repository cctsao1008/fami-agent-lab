from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_reward_beam_probe_imports():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_reward_beam_probe.py"
    spec = spec_from_file_location("fami_pixel_reward_beam_probe_smoke", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.REWARD_BEAM_CHUNKS) >= 6
    assert module._DONE == "RewardBeamProbe: DONE"
