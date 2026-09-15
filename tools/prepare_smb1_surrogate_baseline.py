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
    split_rollout_records_stratified,
    summarize_partition,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare SMB1 surrogate B0 split/evaluation report")
    parser.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args()


def _report_split(split: dict[str, list[dict]]) -> dict:
    return {
        "partitions": {
            name: summarize_partition(rows)
            for name, rows in split.items()
        },
        "candidate_mean_baseline": {
            "validation": candidate_mean_baseline(split["train"], split["validation"]),
            "test": candidate_mean_baseline(split["train"], split["test"]),
        },
    }


def main() -> int:
    args = parse_args()
    records = load_jsonl_records(args.paths)
    stratified = split_rollout_records_stratified(records)
    chronological = split_rollout_records(records)
    report = {
        "model_selection": {
            "split_policy": "stable-hash stratified by root hazard class, grouped by (source,generation)",
            **_report_split(stratified),
        },
        "ood_stress_test": {
            "split_policy": "chronological within source, grouped by (source,generation)",
            **_report_split(chronological),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
