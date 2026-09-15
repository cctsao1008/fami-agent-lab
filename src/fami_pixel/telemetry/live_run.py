"""Durable local evidence bundles for live SMB1 planner runs."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .web_viewer import raw_frame_to_png


def _signed_u8(value: int) -> int:
    value = int(value) & 0xFF
    return value - 256 if value >= 128 else value


def observation_payload(observation) -> dict[str, Any]:
    """Serialize the stable SMB1 observation contract for run evidence."""
    return {
        "native_frame": int(observation.native_frame_id),
        "smb_frame_counter": int(observation.smb_frame_counter),
        "world": int(observation.world),
        "level": int(observation.level),
        "mario_x": int(observation.mario_x_abs),
        "mario_y": int(observation.mario_y),
        "mario_y_high": int(observation.mario_y_high),
        "vx": _signed_u8(observation.player_x_speed),
        "vy": _signed_u8(observation.player_y_speed),
        "player_state": int(observation.player_state),
        "raw_joypad": int(observation.raw_joypad),
        "oper_mode": int(observation.oper_mode),
        "oper_mode_task": int(observation.oper_mode_task),
        "engine": int(observation.game_engine_subroutine),
    }


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


class LiveRunArtifacts:
    """Append planner telemetry and persist one terminal evidence bundle.

    The directory is created at run start so timeline telemetry survives even if
    a later terminal-artifact write fails. Generated content remains under
    ``build/`` and is never a repository source artifact.
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        root: Path = Path("build/live-runs"),
        planner: str,
        started_at: datetime | None = None,
    ) -> None:
        self.started_at = started_at or datetime.now().astimezone()
        stamp = self.started_at.strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe_planner = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in planner.lower())
        self.path = root.expanduser().resolve() / f"{stamp}-{safe_planner}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.timeline_path = self.path / "timeline.jsonl"
        self._timeline_count = 0
        self._finalized = False

    def append_timeline(self, payload: dict) -> None:
        entry = {
            "schema": self.SCHEMA_VERSION,
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            **payload,
        }
        with self.timeline_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, separators=(",", ":"), default=_json_default))
            stream.write("\n")
            stream.flush()
        self._timeline_count += 1

    def finalize(
        self,
        *,
        terminal: str,
        frame,
        observation,
        radar: dict,
        summary: dict,
    ) -> Path:
        """Write the final PNG/JSON evidence exactly once and return the run dir."""
        if self._finalized:
            return self.path

        ended_at = datetime.now().astimezone()
        state = observation_payload(observation)
        radar_payload = dict(radar or {})

        (self.path / "final-frame.png").write_bytes(raw_frame_to_png(frame))
        _write_json(self.path / "final-state.json", state)
        _write_json(self.path / "final-radar.json", radar_payload)
        _write_json(
            self.path / "summary.json",
            {
                "schema": self.SCHEMA_VERSION,
                "terminal": str(terminal),
                "started_at": self.started_at.isoformat(timespec="milliseconds"),
                "ended_at": ended_at.isoformat(timespec="milliseconds"),
                "duration_seconds": round((ended_at - self.started_at).total_seconds(), 3),
                "timeline_records": self._timeline_count,
                "final_state": state,
                "final_radar": {
                    "nearest_enemy_dx": radar_payload.get("nearest_enemy_dx"),
                    "nearest_gap_dx": radar_payload.get("nearest_gap_dx"),
                    "nearest_obstacle_dx": radar_payload.get("nearest_obstacle_dx"),
                    "hazard_ahead": radar_payload.get("hazard_ahead"),
                },
                "files": [
                    "summary.json",
                    "final-frame.png",
                    "final-radar.json",
                    "final-state.json",
                    "timeline.jsonl",
                ],
                **summary,
            },
        )
        self._finalized = True
        return self.path
