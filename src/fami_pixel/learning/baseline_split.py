"""Leakage-resistant train/validation/test splits for SMB1 rollout learning."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Iterable


def _group_key(record: dict) -> tuple[str, int]:
    source = str(record.get("source", ""))
    generation = record.get("generation")
    if generation is None:
        raise ValueError("rollout record is missing generation; grouped split requires root identity")
    return source, int(generation)


def split_rollout_records(
    records: Iterable[dict],
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> dict[str, list[dict]]:
    """Split by whole (source, generation) root groups, never by individual row.

    Each source family is split chronologically on its generation number so live,
    coarse, and precision runs can all contribute to every partition without
    leaking sibling candidates from the same Mesen root across partitions.
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be between 0 and 1")
    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train_fraction + validation_fraction must be < 1")

    by_source: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        source, generation = _group_key(record)
        by_source[source][generation].append(record)

    split = {"train": [], "validation": [], "test": []}
    for source in sorted(by_source):
        generations = sorted(by_source[source])
        n = len(generations)
        if n < 3:
            raise ValueError(f"source {source!r} has only {n} root groups; need at least 3")

        n_train = max(1, int(n * train_fraction))
        n_validation = max(1, int(n * validation_fraction))
        if n_train + n_validation >= n:
            n_validation = max(1, n - n_train - 1)
        if n_train + n_validation >= n:
            n_train = n - n_validation - 1

        assignments = (
            ("train", generations[:n_train]),
            ("validation", generations[n_train : n_train + n_validation]),
            ("test", generations[n_train + n_validation :]),
        )
        for name, group_ids in assignments:
            for generation in group_ids:
                split[name].extend(by_source[source][generation])

    return split


def summarize_partition(records: Iterable[dict]) -> dict:
    rows = list(records)
    groups = {_group_key(row) for row in rows}
    sources = sorted({str(row.get("source", "")) for row in rows})
    delta_x = [int(row["target"]["delta_x"]) for row in rows]
    xs = [int(row["start"]["x"]) for row in rows]
    vxs = [int(row["start"]["vx"]) for row in rows]
    vys = [int(row["start"]["vy"]) for row in rows]
    return {
        "records": len(rows),
        "root_groups": len(groups),
        "sources": sources,
        "death_records": sum(bool(row["target"].get("death")) for row in rows),
        "no_progress_records": sum(bool(row["target"].get("no_progress")) for row in rows),
        "delta_x_mean": mean(delta_x) if delta_x else None,
        "delta_x_min": min(delta_x) if delta_x else None,
        "delta_x_max": max(delta_x) if delta_x else None,
        "start_x_min": min(xs) if xs else None,
        "start_x_max": max(xs) if xs else None,
        "start_vx_min": min(vxs) if vxs else None,
        "start_vx_max": max(vxs) if vxs else None,
        "start_vy_min": min(vys) if vys else None,
        "start_vy_max": max(vys) if vys else None,
    }


def candidate_mean_baseline(train: Iterable[dict], evaluate: Iterable[dict]) -> dict:
    """Cheap B0 reference: predict delta-X from the training mean per candidate."""
    train_rows = list(train)
    eval_rows = list(evaluate)
    global_mean = mean(int(row["target"]["delta_x"]) for row in train_rows)
    buckets: dict[str, list[int]] = defaultdict(list)
    for row in train_rows:
        buckets[str(row["candidate"]["name"])].append(int(row["target"]["delta_x"]))
    candidate_means = {name: mean(values) for name, values in buckets.items()}

    abs_errors = []
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in eval_rows:
        predicted = candidate_means.get(str(row["candidate"]["name"]), global_mean)
        abs_errors.append(abs(predicted - int(row["target"]["delta_x"])))
        grouped[_group_key(row)].append(row)

    ranking_correct = 0
    ranking_groups = 0
    for rows in grouped.values():
        if len(rows) < 2:
            continue
        predicted_best = max(
            rows,
            key=lambda row: candidate_means.get(str(row["candidate"]["name"]), global_mean),
        )
        actual_best = max(rows, key=lambda row: int(row["target"]["delta_x"]))
        ranking_groups += 1
        if predicted_best["candidate"]["name"] == actual_best["candidate"]["name"]:
            ranking_correct += 1

    return {
        "delta_x_mae": mean(abs_errors) if abs_errors else None,
        "ranking_groups": ranking_groups,
        "top1_ranking_accuracy": (ranking_correct / ranking_groups) if ranking_groups else None,
    }
