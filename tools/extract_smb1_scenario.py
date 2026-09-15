#!/usr/bin/env python3
"""Extract one local deterministic SMB1 scenario from a live-run checkpoint set.

The live planner already saves one Mesen state per generation under its isolated
runtime directory. This tool maps a timeline record back to that checkpoint and
copies it into ``build/scenarios/<id>/`` together with a small JSON manifest.

The resulting .mss file remains local because ``build/`` is gitignored.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


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
        # First sighting is the most useful root before applying lead generations.
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract a local SMB1 Mesen regression scenario")
    p.add_argument("timeline", type=Path, help="live-run timeline.jsonl")
    p.add_argument("checkpoint_dir", type=Path, help="matching Runtime IPC run-* directory")
    p.add_argument("scenario_id", help="output scenario id, e.g. star-intercept")
    selector = p.add_mutually_exclusive_group(required=True)
    selector.add_argument("--generation", type=int)
    selector.add_argument("--reward-type", choices=("mushroom", "fire_flower", "star", "one_up"))
    selector.add_argument("--near-x", type=int)
    p.add_argument(
        "--lead-generations",
        type=int,
        default=8,
        help="rewind this many 4-frame live generations before the selected event (default: 8)",
    )
    p.add_argument("--output-root", type=Path, default=Path("build/scenarios"))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    timeline = args.timeline.expanduser().resolve()
    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    records = _load_timeline(timeline)
    selected = _select_record(args, records)

    selected_generation = int(selected.get("generation", -1))
    root_generation = max(0, selected_generation - max(0, args.lead_generations))
    root_record = _record_for_generation(records, root_generation)
    root_generation = int(root_record.get("generation", root_generation))

    source_state = checkpoint_dir / f"live-{root_generation:06d}.mss"
    if not source_state.is_file():
        raise SystemExit(
            "matching checkpoint not found: "
            f"{source_state}\n"
            "Make sure checkpoint_dir is the Runtime IPC directory from the same live run."
        )

    scenario_dir = args.output_root.expanduser().resolve() / args.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    dest_state = scenario_dir / "root.mss"
    shutil.copy2(source_state, dest_state)

    manifest = {
        "schema": "fami-pixel-smb1-scenario-v1",
        "id": args.scenario_id,
        "state_file": "root.mss",
        "root_generation": root_generation,
        "native_frame": root_record.get("native_frame"),
        "mario_x": root_record.get("mario_x"),
        "mario_y": root_record.get("mario_y"),
        "selection_generation": selected_generation,
        "selection_native_frame": selected.get("native_frame"),
        "selection_mario_x": selected.get("mario_x"),
        "selection_reward_type": selected.get("reward_target_type"),
        "selection_reward_dx": selected.get("reward_target_dx"),
        "source_timeline": str(timeline),
        "source_checkpoint_dir": str(checkpoint_dir),
    }
    manifest_path = scenario_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Scenario   : {args.scenario_id}")
    print(f"Generation : {root_generation} (selected {selected_generation}, lead {args.lead_generations})")
    print(f"Mario      : X={manifest['mario_x']} frame={manifest['native_frame']}")
    print(f"State      : {dest_state}")
    print(f"Manifest   : {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
