#!/usr/bin/env python3
"""Extract one local deterministic SMB1 scenario from a live-run checkpoint set.

The live planner writes one timeline record and then saves the matching Mesen
checkpoint under the *next* numeric generation. Current V22 supervisors also
preserve historical ``live-*.mss`` files under ``<runtime>/archive/`` before the
rolling planner prunes them.

This tool maps a timeline event back to that exact preserved Mesen state and
copies it into ``build/scenarios/<id>/`` with a JSON manifest. Pass ``auto`` as
the checkpoint directory to discover the matching recent ``run-*`` directory.
The resulting .mss file remains local because ``build/`` is gitignored.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from fami_pixel.runtime.checkpoint_archive import (
    available_checkpoint_generations,
    resolve_checkpoint,
)


def _load_timeline(path: Path) -> list[dict]:
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON at {path}:{line_number}: {exc}") from exc
    if not records:
        raise SystemExit(f"timeline is empty: {path}")
    return records


def _select_record(args, records: list[dict]) -> dict:
    if args.generation is not None:
        matches = [r for r in records if int(r.get("generation", -1)) == args.generation]
        if not matches:
            raise SystemExit(f"generation {args.generation} not found in timeline")
        return matches[0]

    if args.reward_type is not None:
        matches = [
            r for r in records
            if str(r.get("reward_target_type") or "") == args.reward_type
        ]
        if not matches:
            raise SystemExit(f"reward type {args.reward_type!r} not found in timeline")
        return min(matches, key=lambda r: int(r.get("generation", 1 << 30)))

    if args.near_x is not None:
        with_x = [r for r in records if r.get("mario_x") is not None]
        if not with_x:
            raise SystemExit("timeline has no mario_x values")
        return min(with_x, key=lambda r: abs(int(r["mario_x"]) - args.near_x))

    raise SystemExit("choose one of --generation, --reward-type, or --near-x")


def _record_for_generation(records: list[dict], generation: int) -> dict:
    exact = [r for r in records if int(r.get("generation", -1)) == generation]
    if exact:
        return exact[0]
    older = [r for r in records if int(r.get("generation", -1)) <= generation]
    if not older:
        raise SystemExit(f"no timeline record at or before generation {generation}")
    return max(older, key=lambda r: int(r.get("generation", -1)))


def timeline_to_checkpoint_generation(timeline_generation: int) -> int:
    """Map a V17+ timeline generation to its immediately following checkpoint."""
    return int(timeline_generation) + 1


def checkpoint_to_timeline_generation(checkpoint_generation: int) -> int:
    """Inverse of :func:`timeline_to_checkpoint_generation` for saved live roots."""
    return max(0, int(checkpoint_generation) - 1)


def _exact_checkpoint(runtime_dir: Path, generation: int) -> Path | None:
    runtime = Path(runtime_dir).expanduser().resolve()
    for folder in (runtime, runtime / "archive"):
        candidate = folder / f"live-{int(generation):06d}.mss"
        if candidate.is_file():
            return candidate
    return None


def discover_checkpoint_dir(
    timeline: Path,
    checkpoint_generation: int,
    *,
    search_root: Path = Path("build/checkpoints/v11-live"),
) -> Path:
    """Find the run-* directory most likely to belong to this evidence timeline."""
    root = search_root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"checkpoint search root not found: {root}")

    timeline_mtime = timeline.stat().st_mtime_ns
    candidates: list[tuple[int, int, Path]] = []
    for runtime in root.glob("run-*"):
        checkpoint = _exact_checkpoint(runtime, checkpoint_generation)
        if checkpoint is None:
            continue
        try:
            checkpoint_mtime = checkpoint.stat().st_mtime_ns
            runtime_mtime = runtime.stat().st_mtime_ns
        except OSError:
            continue
        candidates.append((abs(checkpoint_mtime - timeline_mtime), -runtime_mtime, runtime))

    if not candidates:
        raise SystemExit(
            "could not auto-discover a Runtime IPC directory containing exact checkpoint "
            f"live-{checkpoint_generation:06d}.mss under {root}"
        )

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2].resolve()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract a local SMB1 Mesen regression scenario")
    p.add_argument("timeline", type=Path, help="live-run timeline.jsonl")
    p.add_argument(
        "checkpoint_dir",
        help="matching Runtime IPC run-* directory, or literal 'auto'",
    )
    p.add_argument("scenario_id", help="output scenario id, e.g. star-intercept")
    selector = p.add_mutually_exclusive_group(required=True)
    selector.add_argument("--generation", type=int)
    selector.add_argument("--reward-type", choices=("mushroom", "fire_flower", "star", "one_up"))
    selector.add_argument("--near-x", type=int)
    p.add_argument(
        "--lead-generations",
        type=int,
        default=8,
        help="rewind this many 4-frame timeline generations before the selected event (default: 8)",
    )
    p.add_argument("--output-root", type=Path, default=Path("build/scenarios"))
    p.add_argument(
        "--checkpoint-search-root",
        type=Path,
        default=Path("build/checkpoints/v11-live"),
        help="root containing run-* directories used when checkpoint_dir=auto",
    )
    return p.parse_args()


def _missing_checkpoint_message(checkpoint_dir: Path, desired_checkpoint_generation: int) -> str:
    available = available_checkpoint_generations(checkpoint_dir)
    available_text = (
        "none"
        if not available
        else f"{available[0]}..{available[-1]} ({len(available)} states)"
    )
    return (
        "no preserved checkpoint at or before checkpoint generation "
        f"{desired_checkpoint_generation}\n"
        f"Runtime IPC: {checkpoint_dir}\n"
        f"Available  : {available_text}\n"
    )


def main() -> int:
    args = parse_args()
    timeline = args.timeline.expanduser().resolve()
    records = _load_timeline(timeline)
    selected = _select_record(args, records)

    selected_timeline_generation = int(selected.get("generation", -1))
    desired_timeline_generation = max(
        0,
        selected_timeline_generation - max(0, args.lead_generations),
    )
    desired_record = _record_for_generation(records, desired_timeline_generation)
    desired_timeline_generation = int(
        desired_record.get("generation", desired_timeline_generation)
    )
    desired_checkpoint_generation = timeline_to_checkpoint_generation(
        desired_timeline_generation
    )

    if str(args.checkpoint_dir).lower() == "auto":
        checkpoint_dir = discover_checkpoint_dir(
            timeline,
            desired_checkpoint_generation,
            search_root=args.checkpoint_search_root,
        )
        print(f"Runtime IPC : {checkpoint_dir} (auto)")
    else:
        checkpoint_dir = Path(args.checkpoint_dir).expanduser().resolve()

    source_state, actual_checkpoint_generation = resolve_checkpoint(
        checkpoint_dir,
        desired_checkpoint_generation,
    )
    if source_state is None or actual_checkpoint_generation is None:
        raise SystemExit(
            _missing_checkpoint_message(checkpoint_dir, desired_checkpoint_generation)
        )

    actual_timeline_generation = checkpoint_to_timeline_generation(
        actual_checkpoint_generation
    )
    root_record = _record_for_generation(records, actual_timeline_generation)
    root_timeline_generation = int(
        root_record.get("generation", actual_timeline_generation)
    )

    scenario_dir = args.output_root.expanduser().resolve() / args.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    dest_state = scenario_dir / "root.mss"
    shutil.copy2(source_state, dest_state)

    manifest = {
        "schema": "fami-pixel-smb1-scenario-v2",
        "id": args.scenario_id,
        "state_file": "root.mss",
        "root_generation": root_timeline_generation,
        "root_checkpoint_generation": actual_checkpoint_generation,
        "requested_root_generation": desired_timeline_generation,
        "requested_checkpoint_generation": desired_checkpoint_generation,
        "native_frame": root_record.get("native_frame"),
        "mario_x": root_record.get("mario_x"),
        "mario_y": root_record.get("mario_y"),
        "selection_generation": selected_timeline_generation,
        "selection_native_frame": selected.get("native_frame"),
        "selection_mario_x": selected.get("mario_x"),
        "selection_reward_type": selected.get("reward_target_type"),
        "selection_reward_dx": selected.get("reward_target_dx"),
        "source_timeline": str(timeline),
        "source_checkpoint_dir": str(checkpoint_dir),
        "source_checkpoint": str(source_state),
    }
    manifest_path = scenario_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Scenario   : {args.scenario_id}")
    if actual_checkpoint_generation != desired_checkpoint_generation:
        print(
            "Checkpoint : fallback checkpoint generation "
            f"{actual_checkpoint_generation} (requested {desired_checkpoint_generation})"
        )
    print(
        f"Timeline   : {root_timeline_generation} "
        f"(selected {selected_timeline_generation}, lead {args.lead_generations})"
    )
    print(f"Checkpoint : {actual_checkpoint_generation}")
    print(f"Mario      : X={manifest['mario_x']} frame={manifest['native_frame']}")
    print(f"Source     : {source_state}")
    print(f"State      : {dest_state}")
    print(f"Manifest   : {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
