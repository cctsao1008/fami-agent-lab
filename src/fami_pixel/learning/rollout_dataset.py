"""Durable supervised records for SMB1 transition/risk surrogate learning."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Iterable

from fami_pixel.games.smb1 import CandidateOutcome, Smb1Observation


ROLLOUT_SCHEMA_VERSION = 1


def _signed_u8(value: int) -> int:
    value = int(value) & 0xFF
    return value - 256 if value >= 128 else value


def _observation_payload(observation: Smb1Observation) -> dict:
    return {
        "native_frame": int(observation.native_frame_id),
        "world": int(observation.world),
        "level": int(observation.level),
        "x": int(observation.mario_x_abs),
        "y": int(observation.mario_y),
        "y_high": int(observation.mario_y_high),
        "vx": _signed_u8(observation.player_x_speed),
        "vy": _signed_u8(observation.player_y_speed),
        "player_state": int(observation.player_state),
        "engine": int(observation.game_engine_subroutine),
        "joypad": int(observation.raw_joypad),
    }


def _schedule_payload(outcome: CandidateOutcome) -> list[dict[str, int]]:
    return [
        {
            "buttons": int(command.nes_buttons),
            "frames": int(command.frame_count),
        }
        for command in outcome.candidate.commands
    ]


def build_rollout_record(
    start: Smb1Observation,
    end: Smb1Observation,
    outcome: CandidateOutcome,
    *,
    source: str,
    generation: int | None = None,
    worker: int | None = None,
    probe_terminal: str | None = None,
    probe_frames: int | None = None,
) -> dict:
    """Build one teacher sample from a real Mesen counterfactual rollout.

    ``probe_terminal`` is optional additive metadata from a second authoritative
    neutral continuation that starts at the candidate end state. Older schema-1
    rows simply lack these optional probe fields.
    """
    death = outcome.terminal.value == "death"
    level_complete = outcome.terminal.value == "level_complete"
    no_progress = outcome.progress <= 0
    descending_low = _signed_u8(end.player_y_speed) > 0 and int(end.mario_y) >= 178
    doomed = (not death) and probe_terminal == "death"

    target = {
        "delta_x": int(end.mario_x_abs - start.mario_x_abs),
        "delta_y": int(end.mario_y - start.mario_y),
        "end_vx": _signed_u8(end.player_x_speed),
        "end_vy": _signed_u8(end.player_y_speed),
        "max_x": int(outcome.max_x),
        "elapsed_frames": int(outcome.elapsed_frames),
        "terminal": outcome.terminal.value,
        "death": bool(death),
        "level_complete": bool(level_complete),
        "reached_flagpole": bool(outcome.reached_flagpole),
        "no_progress": bool(no_progress),
        "descending_low": bool(descending_low),
        "doomed_within_probe": bool(doomed),
    }
    if probe_terminal is not None:
        target["probe_terminal"] = str(probe_terminal)
        target["probe_frames"] = int(probe_frames or 0)

    return {
        "schema": ROLLOUT_SCHEMA_VERSION,
        "source": source,
        "generation": generation,
        "worker": worker,
        "candidate": {
            "name": outcome.candidate.name,
            "horizon_frames": int(outcome.candidate.frame_count),
            "schedule": _schedule_payload(outcome),
        },
        "start": _observation_payload(start),
        "end": _observation_payload(end),
        "target": target,
    }


def write_jsonl_record(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
        stream.write("\n")


def load_jsonl_records(paths: Iterable[Path]) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        with Path(path).open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if int(record.get("schema", -1)) != ROLLOUT_SCHEMA_VERSION:
                    raise ValueError(
                        f"{path}:{line_number}: unsupported rollout schema "
                        f"{record.get('schema')!r}"
                    )
                records.append(record)
    return records


def summarize_rollout_records(records: Iterable[dict]) -> dict:
    rows = list(records)
    candidates = Counter(str(row["candidate"]["name"]) for row in rows)
    terminals = Counter(str(row["target"]["terminal"]) for row in rows)
    deaths = sum(bool(row["target"].get("death")) for row in rows)
    doomed = sum(bool(row["target"].get("doomed_within_probe")) for row in rows)
    probed = sum("probe_terminal" in row["target"] for row in rows)
    no_progress = sum(bool(row["target"].get("no_progress")) for row in rows)
    descending_low = sum(bool(row["target"].get("descending_low")) for row in rows)
    delta_x = [int(row["target"]["delta_x"]) for row in rows]

    return {
        "records": len(rows),
        "candidate_counts": dict(sorted(candidates.items())),
        "terminal_counts": dict(sorted(terminals.items())),
        "death_records": deaths,
        "doomed_within_probe_records": doomed,
        "probed_records": probed,
        "no_progress_records": no_progress,
        "descending_low_records": descending_low,
        "delta_x_min": min(delta_x) if delta_x else None,
        "delta_x_max": max(delta_x) if delta_x else None,
        "delta_x_mean": (sum(delta_x) / len(delta_x)) if delta_x else None,
    }
