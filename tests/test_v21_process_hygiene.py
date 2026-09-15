import importlib.util
from pathlib import Path


def _load_v21():
    path = Path(__file__).resolve().parents[1] / "examples" / "mesen_smb_checkpoint_planner_v21.py"
    spec = importlib.util.spec_from_file_location("fami_pixel_v21", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_shadow_pids_from_authority_log():
    v21 = _load_v21()

    assert v21._parse_shadow_pids(
        "[17:44:00.000] Shadow PIDs : 12001, 12002, 12003, 12004\n"
    ) == (12001, 12002, 12003, 12004)


def test_parse_shadow_pids_ignores_unrelated_lines():
    v21 = _load_v21()

    assert v21._parse_shadow_pids("Planner V20: landing-zone safety enabled\n") == ()
