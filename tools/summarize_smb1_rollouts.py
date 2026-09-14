#!/usr/bin/env python3
"""Validate and summarize SMB1 rollout JSONL datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fami_pixel.learning import load_jsonl_records, summarize_rollout_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize SMB1 surrogate-learning rollout datasets")
    parser.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_jsonl_records(args.paths)
    print(json.dumps(summarize_rollout_records(records), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
