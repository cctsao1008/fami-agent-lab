from collections import defaultdict


def _summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["source"], row["generation"])].append(row)
    death = sum(any(r["target"].get("death") for r in group) for group in groups.values())
    doomed = sum(
        any(r["target"].get("doomed_within_probe") for r in group)
        for group in groups.values()
    )
    risk = sum(
        any(
            r["target"].get("death") or r["target"].get("doomed_within_probe")
            for r in group
        )
        for group in groups.values()
    )
    no_progress = sum(
        any(r["target"].get("no_progress") for r in group)
        for group in groups.values()
    )
    return len(groups), death, doomed, risk, no_progress


def test_hazard_counts_are_by_root_group_not_row():
    rows = [
        {
            "source": "haz",
            "generation": 0,
            "target": {"death": True, "doomed_within_probe": False, "no_progress": False},
        },
        {
            "source": "haz",
            "generation": 0,
            "target": {"death": True, "doomed_within_probe": False, "no_progress": True},
        },
        {
            "source": "haz",
            "generation": 1,
            "target": {"death": False, "doomed_within_probe": True, "no_progress": True},
        },
        {
            "source": "haz",
            "generation": 2,
            "target": {"death": False, "doomed_within_probe": False, "no_progress": False},
        },
    ]

    assert _summarize(rows) == (3, 1, 1, 2, 2)
