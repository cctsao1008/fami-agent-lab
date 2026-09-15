from fami_pixel.learning.baseline_split import (
    candidate_mean_baseline,
    split_rollout_records,
)


def _row(source: str, generation: int, candidate: str, delta_x: int, *, death=False, no_progress=False):
    return {
        "schema": 1,
        "source": source,
        "generation": generation,
        "candidate": {"name": candidate, "horizon_frames": 8, "schedule": []},
        "start": {"x": generation * 10, "y": 176, "vx": 1, "vy": 0},
        "target": {
            "delta_x": delta_x,
            "death": death,
            "no_progress": no_progress,
        },
    }


def test_split_keeps_root_candidates_together_and_preserves_sources():
    rows = []
    for source in ("offline-live", "offline-precision"):
        for generation in range(10):
            rows.append(_row(source, generation, "a", generation))
            rows.append(_row(source, generation, "b", generation + 1))

    split = split_rollout_records(rows)

    memberships = {}
    for partition, partition_rows in split.items():
        for row in partition_rows:
            key = (row["source"], row["generation"])
            memberships.setdefault(key, set()).add(partition)

    assert all(len(parts) == 1 for parts in memberships.values())
    assert {row["source"] for row in split["train"]} == {"offline-live", "offline-precision"}
    assert {row["source"] for row in split["validation"]} == {"offline-live", "offline-precision"}
    assert {row["source"] for row in split["test"]} == {"offline-live", "offline-precision"}


def test_candidate_mean_baseline_reports_mae_and_grouped_ranking():
    train = [
        _row("offline-live", 0, "slow", 1),
        _row("offline-live", 0, "fast", 5),
        _row("offline-live", 1, "slow", 2),
        _row("offline-live", 1, "fast", 6),
    ]
    evaluate = [
        _row("offline-live", 2, "slow", 3),
        _row("offline-live", 2, "fast", 7),
        _row("offline-live", 3, "slow", 2),
        _row("offline-live", 3, "fast", 8),
    ]

    result = candidate_mean_baseline(train, evaluate)

    assert result["ranking_groups"] == 2
    assert result["top1_ranking_accuracy"] == 1.0
    assert result["delta_x_mae"] >= 0.0
