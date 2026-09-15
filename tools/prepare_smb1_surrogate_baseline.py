#!/usr/bin/env python3
"""Prepare leakage-resistant SMB1 surrogate baseline partitions and metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fami_pixel.learning import load_jsonl_records
from fami_pixel.learning.baseline_split import (
    candidate_mean_baseline,
    split_rollout_records,
    summarize_partition,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare SMB1 surrogate B0 split/evaluation report")
    parser.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_jsonl_records(args.paths)
    split = split_rollout_records(records)
    report = {
        "split_policy": "chronological within source, grouped by (source,generation)",
        "partitions": {
            name: summarize_partition(rows)
            for name, rows in split.items()
        },
        "candidate_mean_baseline": {
            "validation": candidate_mean_baseline(split["train"], split["validation"]),
            "test": candidate_mean_baseline(split["train"], split["test"]),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
