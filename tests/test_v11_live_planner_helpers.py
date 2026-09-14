from pathlib import Path
import importlib.util


MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "mesen_smb_checkpoint_planner_v11.py"
spec = importlib.util.spec_from_file_location("planner_v11", MODULE_PATH)
v11 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(v11)


def test_candidate_shards_cover_pool_without_overlap():
    pool = tuple(candidate.name for candidate in v11._candidate_pool())
    shards = [
        tuple(candidate.name for candidate in v11._candidate_shard(index, 4))
        for index in range(4)
    ]
    flattened = tuple(name for shard in shards for name in shard)

    assert set(flattened) == set(pool)
    assert len(flattened) == len(pool)
    assert all(shard for shard in shards)


def test_best_fresh_plan_prefers_newest_root_then_score(tmp_path):
    responses = [tmp_path / "r0.json", tmp_path / "r1.json"]
    v11._atomic_json(
        responses[0],
        {
            "generation": 2,
            "root_frame": 100,
            "candidate": "run_long_jump",
            "buttons": 1,
            "score": [1, 0, 90, 190],
        },
    )
    v11._atomic_json(
        responses[1],
        {
            "generation": 3,
            "root_frame": 104,
            "candidate": "run",
            "buttons": 2,
            "score": [1, 0, 40, 144],
        },
    )

    plan = v11._best_fresh_plan(responses, current_frame=108, freshness=8, last_applied_generation=-1)
    assert plan is not None
    assert plan["candidate"] == "run"
    assert plan["age"] == 4


def test_best_fresh_plan_rejects_stale_and_already_applied(tmp_path):
    responses = [tmp_path / "r0.json", tmp_path / "r1.json"]
    v11._atomic_json(
        responses[0],
        {
            "generation": 2,
            "root_frame": 90,
            "candidate": "run",
            "buttons": 1,
            "score": [1, 0, 50, 140],
        },
    )
    v11._atomic_json(
        responses[1],
        {
            "generation": 3,
            "root_frame": 104,
            "candidate": "tap_jump",
            "buttons": 2,
            "score": [1, 0, 30, 134],
        },
    )

    assert v11._best_fresh_plan(
        responses,
        current_frame=108,
        freshness=8,
        last_applied_generation=3,
    ) is None
