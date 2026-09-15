#!/usr/bin/env python3
"""Train and evaluate the dependency-free SMB1 surrogate MLP baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fami_pixel.learning import load_jsonl_records
from fami_pixel.learning.baseline_split import (
    split_rollout_records,
    split_rollout_records_stratified,
)
from fami_pixel.learning.tiny_mlp import TinySurrogateMLP, evaluate_model, feature_vector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train offline SMB1 tiny surrogate MLP baseline")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--hidden", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=800)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=22)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_jsonl_records(args.paths)
    model_split = split_rollout_records_stratified(records)
    ood_split = split_rollout_records(records)

    input_size = len(feature_vector(model_split["train"][0]))
    model = TinySurrogateMLP(input_size, args.hidden, seed=args.seed)
    model.fit(
        model_split["train"],
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        seed=args.seed,
    )

    report = {
        "architecture": {
            "input_features": input_size,
            "hidden_units": args.hidden,
            "outputs": ["delta_x", "death_probability", "no_progress_probability"],
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
            "seed": args.seed,
        },
        "authority_boundary": "offline prediction only; Mesen remains authoritative",
        "model_selection": {
            "train": evaluate_model(model, model_split["train"]),
            "validation": evaluate_model(model, model_split["validation"]),
            "test": evaluate_model(model, model_split["test"]),
        },
        "ood_stress_test": {
            "chronological_test": evaluate_model(model, ood_split["test"]),
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
