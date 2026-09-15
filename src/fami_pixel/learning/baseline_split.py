"""Leakage-resistant train/validation/test splits for SMB1 rollout learning."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from statistics import mean
from typing import Iterable


def _group_key(record: dict) -> tuple[str, int]:
    source = str(record.get("source", ""))
    generation = record.get("generation")
    if generation is None:
        raise ValueError("rollout record is missing generation; grouped split requires root identity")
    return source, int(generation)


def _partition_counts(n: int, train_fraction: float, validation_fraction: float) -> tuple[int, int, int]:
    if n <= 0:
        return 0, 0, 0
    if n == 1:
        return 1, 0, 0
    if n == 2:
        return 1, 0, 1

    n_train = max(1, int(n * train_fraction))
    n_validation = max(1, int(n * validation_fraction))
    if n_train + n_validation >= n:
        n_validation = max(1, n - n_train - 1)
    if n_train + n_validation >= n:
        n_train = n - n_validation - 1
    return n_train, n_validation, n - n_train - n_validation


def _validate_fractions(train_fraction: float, validation_fraction: float) -> None:
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be between 0 and 1")
    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train_fraction + validation_fraction must be < 1")


def split_rollout_records(
    records: Iterable[dict],
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> dict[str, list[dict]]:
    """Chronological grouped split used as an out-of-distribution stress test.

    Each source family is split chronologically on generation number. Sibling
    candidates from one Mesen root always stay together, but late trajectory
    hazards can therefore concentrate in validation/test. This behavior is
    intentional for OOD evaluation and should not be the only model-selection
    split on small datasets.
    """
    _validate_fractions(train_fraction, validation_fraction)

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
        n_train, n_validation, _ = _partition_counts(n, train_fraction, validation_fraction)
        assignments = (
            ("train", generations[:n_train]),
            ("validation", generations[n_train : n_train + n_validation]),
            ("test", generations[n_train + n_validation :]),
        )
        for name, group_ids in assignments:
            for generation in group_ids:
                split[name].extend(by_source[source][generation])

    return split


def _hazard_class(rows: list[dict]) -> str:
    if any(bool(row["target"].get("death")) for row in rows):
        return "death"
    if any(bool(row["target"].get("no_progress")) for row in rows):
        return "no_progress"
    return "neutral"


def _stable_group_order(key: tuple[str, int]) -> str:
    return hashlib.sha256(f"{key[0]}:{key[1]}".encode("utf-8")).hexdigest()


def split_rollout_records_stratified(
    records: Iterable[dict],
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> dict[str, list[dict]]:
    """Deterministic grouped split stratified by root-level hazard class.

    The unit of assignment remains the whole (source, generation) Mesen root,
    so sibling candidate rollouts cannot leak across partitions. Root groups are
    bucketed as death, no-progress, or neutral and deterministically shuffled by
    a stable hash before each bucket is partitioned. This gives model-selection
    partitions hazard coverage when the dataset contains at least three groups
    in a hazard class, while the chronological split remains available as a
    separate distribution-shift stress test.
    """
    _validate_fractions(train_fraction, validation_fraction)

    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for record in records:
        grouped[_group_key(record)].append(record)

    if len(grouped) < 3:
        raise ValueError("need at least 3 root groups for stratified split")

    buckets: dict[str, list[tuple[tuple[str, int], list[dict]]]] = defaultdict(list)
    for key, rows in grouped.items():
        buckets[_hazard_class(rows)].append((key, rows))

    split = {"train": [], "validation": [], "test": []}
    for hazard in ("death", "no_progress", "neutral"):
        items = buckets.get(hazard, [])
        items.sort(key=lambda item: _stable_group_order(item[0]))
        n_train, n_validation, _ = _partition_counts(len(items), train_fraction, validation_fraction)
        sections = (
            ("train", items[:n_train]),
            ("validation", items[n_train : n_train + n_validation]),
            ("test", items[n_train + n_validation :]),
        )
        for partition, assigned in sections:
            for _, rows in assigned:
                split[partition].extend(rows)

    return split


def summarize_partition(records: Iterable[dict]) -> dict:
    rows = list(records)
    groups = {_group_key(row) for row in rows}
    grouped_rows: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped_rows[_group_key(row)].append(row)
    sources = sorted({str(row.get("source", "")) for row in rows})
    delta_x = [int(row["target"]["delta_x"]) for row in rows]
    xs = [int(row["start"]["x"]) for row in rows]
    vxs = [int(row["start"]["vx"]) for row in rows]
    vys = [int(row["start"]["vy"]) for row in rows]
    hazard_groups = defaultdict(int)
    for root_rows in grouped_rows.values():
        hazard_groups[_hazard_class(root_rows)] += 1
    return {
        "records": len(rows),
        "root_groups": len(groups),
        "sources": sources,
        "death_groups": hazard_groups["death"],
        "no_progress_groups": hazard_groups["no_progress"],
        "neutral_groups": hazard_groups["neutral"],
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
