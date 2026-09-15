#!/usr/bin/env python3
"""Summarize distinct hazard root groups in SMB1 rollout datasets."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from fami_pixel.learning import load_jsonl_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize SMB1 hazard root-group coverage")
    parser.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_jsonl_records(args.paths)
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in records:
        groups[(str(row.get("source", "")), int(row["generation"]))].append(row)

    death_groups = []
    doomed_groups = []
    risk_groups = []
    no_progress_groups = []
    mixed_groups = []
    per_source: dict[str, dict[str, int]] = defaultdict(lambda: {
        "root_groups": 0,
        "death_groups": 0,
        "doomed_groups": 0,
        "risk_groups": 0,
        "no_progress_groups": 0,
    })

    for (source, generation), rows in sorted(groups.items()):
        has_death = any(bool(row["target"].get("death")) for row in rows)
        has_doomed = any(bool(row["target"].get("doomed_within_probe")) for row in rows)
        has_risk = has_death or has_doomed
        has_no_progress = any(bool(row["target"].get("no_progress")) for row in rows)
        per_source[source]["root_groups"] += 1
        if has_death:
            death_groups.append((source, generation))
            per_source[source]["death_groups"] += 1
        if has_doomed:
            doomed_groups.append((source, generation))
            per_source[source]["doomed_groups"] += 1
        if has_risk:
            risk_groups.append((source, generation))
            per_source[source]["risk_groups"] += 1
        if has_no_progress:
            no_progress_groups.append((source, generation))
            per_source[source]["no_progress_groups"] += 1
        if has_risk and has_no_progress:
            mixed_groups.append((source, generation))

    report = {
        "records": len(records),
        "root_groups": len(groups),
        "death_groups": len(death_groups),
        "doomed_groups": len(doomed_groups),
        "risk_groups": len(risk_groups),
        "no_progress_groups": len(no_progress_groups),
        "mixed_hazard_groups": len(mixed_groups),
        "per_source": dict(sorted(per_source.items())),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
