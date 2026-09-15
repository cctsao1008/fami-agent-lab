from __future__ import annotations

import importlib.util
import os
from pathlib import Path


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "extract_smb1_scenario.py"
_SPEC = importlib.util.spec_from_file_location("extract_smb1_scenario", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_timeline_generation_maps_to_following_checkpoint() -> None:
    assert _MODULE.timeline_to_checkpoint_generation(0) == 1
    assert _MODULE.timeline_to_checkpoint_generation(185) == 186
    assert _MODULE.checkpoint_to_timeline_generation(186) == 185


def test_auto_discovery_prefers_checkpoint_nearest_timeline_time(tmp_path: Path) -> None:
    search_root = tmp_path / "v11-live"
    older = search_root / "run-old" / "archive"
    newer = search_root / "run-new" / "archive"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)

    old_state = older / "live-000186.mss"
    new_state = newer / "live-000186.mss"
    old_state.write_bytes(b"old")
    new_state.write_bytes(b"new")

    timeline = tmp_path / "timeline.jsonl"
    timeline.write_text("{}\n", encoding="utf-8")

    base = 1_800_000_000
    os.utime(old_state, (base - 100, base - 100))
    os.utime(new_state, (base - 2, base - 2))
    os.utime(timeline, (base, base))

    discovered = _MODULE.discover_checkpoint_dir(
        timeline,
        186,
        search_root=search_root,
    )
    assert discovered == newer.parent.resolve()
