from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "extract_smb1_scenario.py"
_SPEC = spec_from_file_location("extract_smb1_scenario_test", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_stable_root_rewinds_past_already_falling_records() -> None:
    records = [
        {"generation": 8, "mario_y": 176, "radar": {"grounded": True}},
        {"generation": 9, "mario_y": 208, "radar": {"grounded": False}},
        {"generation": 10, "mario_y": 243, "radar": {"grounded": False}},
    ]

    chosen = _MOD._stable_record_at_or_before(records, 10)
    assert chosen["generation"] == 8


def test_grounded_fallback_requires_native_height_and_zero_vy() -> None:
    assert _MOD._record_grounded(
        {
            "mario_y": 176,
            "radar": {"player_y_high": 1, "player_vy": 0},
        }
    )
    assert not _MOD._record_grounded(
        {
            "mario_y": 243,
            "radar": {"player_y_high": 1, "player_vy": 4},
        }
    )
