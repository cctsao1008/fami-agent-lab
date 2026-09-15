from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from fami_pixel.adapters.mesen import MesenLoadError


def _load_probe_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "smb1_reward_beam_probe.py"
    module_name = "fami_pixel_reward_beam_probe_smoke"
    spec = spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)

    # Python 3.13's dataclasses implementation resolves postponed string
    # annotations through sys.modules while the class decorator runs.  A normal
    # import inserts the module before execution; importlib smoke tests must do
    # the same or @dataclass can fail even though the script itself is valid.
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    return module


def test_reward_beam_probe_imports():
    module = _load_probe_module()
    assert len(module.REWARD_BEAM_CHUNKS) >= 6
    assert module._DONE == "RewardBeamProbe: DONE"


def test_reward_beam_probe_classifies_only_native_step_timeouts_as_recoverable():
    module = _load_probe_module()
    assert module._is_native_step_timeout(
        MesenLoadError("FamiPixelStepFrame failed (4: timeout waiting for native frame advance).")
    )
    assert module._is_native_step_timeout(
        MesenLoadError("FamiPixelStepFrame failed (5: timeout waiting for the new debugger stop).")
    )
    assert not module._is_native_step_timeout(
        MesenLoadError("FamiPixelStepFrame failed (1: emulator is not running).")
    )
