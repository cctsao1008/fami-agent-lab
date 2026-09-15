from collections import defaultdict


def _summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["source"], row["generation"])].append(row)
    death = sum(any(r["target"].get("death") for r in group) for group in groups.values())
    no_progress = sum(any(r["target"].get("no_progress") for r in group) for group in groups.values())
    return len(groups), death, no_progress


def test_hazard_counts_are_by_root_group_not_row():
    rows = [
        {"source": "haz", "generation": 0, "target": {"death": True, "no_progress": False}},
        {"source": "haz", "generation": 0, "target": {"death": True, "no_progress": True}},
        {"source": "haz", "generation": 1, "target": {"death": False, "no_progress": True}},
    ]

    assert _summarize(rows) == (2, 1, 2)
