from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from fami_pixel.telemetry import LiveRunArtifacts


def _observation():
    return SimpleNamespace(
        native_frame_id=337,
        smb_frame_counter=81,
        world=0,
        level=0,
        mario_x_abs=316,
        mario_y=176,
        mario_y_high=1,
        player_state=0,
        player_x_speed=40,
        player_y_speed=0,
        raw_joypad=0x83,
        oper_mode=1,
        oper_mode_task=3,
        game_engine_subroutine=0x06,
    )


def test_live_run_artifacts_persist_terminal_bundle(tmp_path: Path):
    recorder = LiveRunArtifacts(
        root=tmp_path,
        planner="v17-test",
        started_at=datetime(2026, 9, 15, 15, 45, tzinfo=timezone.utc),
    )
    recorder.append_timeline(
        {
            "generation": 12,
            "native_frame": 333,
            "action": "RIGHT+B 1f -> RIGHT+A+B 11f",
            "radar": {"nearest_enemy_dx": 41},
        }
    )

    frame = SimpleNamespace(width=2, height=1, pixels=(0, 1))
    path = recorder.finalize(
        terminal="death",
        frame=frame,
        observation=_observation(),
        radar={
            "nearest_enemy_dx": 5,
            "nearest_gap_dx": None,
            "nearest_obstacle_dx": None,
            "hazard_ahead": True,
        },
        summary={"planner": "v17-test", "final_action": "run"},
    )

    assert path == recorder.path
    assert (path / "final-frame.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (path / "timeline.jsonl").read_text(encoding="utf-8").count("\n") == 1

    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    assert summary["terminal"] == "death"
    assert summary["timeline_records"] == 1
    assert summary["final_state"]["mario_x"] == 316
    assert summary["final_radar"]["nearest_enemy_dx"] == 5
    assert summary["planner"] == "v17-test"


def test_live_run_finalize_is_idempotent(tmp_path: Path):
    recorder = LiveRunArtifacts(root=tmp_path, planner="v17-test")
    frame = SimpleNamespace(width=1, height=1, pixels=(0,))
    first = recorder.finalize(
        terminal="frame_limit",
        frame=frame,
        observation=_observation(),
        radar={},
        summary={"planner": "v17-test"},
    )
    second = recorder.finalize(
        terminal="death",
        frame=frame,
        observation=_observation(),
        radar={"nearest_enemy_dx": 1},
        summary={"planner": "should-not-overwrite"},
    )

    assert second == first
    summary = json.loads((first / "summary.json").read_text(encoding="utf-8"))
    assert summary["terminal"] == "frame_limit"
    assert summary["planner"] == "v17-test"
