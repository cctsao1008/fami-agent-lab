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


def test_live_candidate_pool_is_short_horizon():
    pool = v11._candidate_pool()
    assert tuple(candidate.name for candidate in pool) == v11.LIVE_CANDIDATE_NAMES
    assert max(candidate.frame_count for candidate in pool) <= 12


def test_best_fresh_plan_prefers_newest_root_then_score(tmp_path):
    responses = [tmp_path / "r0.json", tmp_path / "r1.json"]
    v11._atomic_json(
        responses[0],
        {
            "generation": 2,
            "root_frame": 100,
            "candidate": "right_a_b_12",
            "schedule": [{"buttons": 0x83, "frames": 12}],
            "score": [1, 0, 90, 190],
        },
    )
    v11._atomic_json(
        responses[1],
        {
            "generation": 3,
            "root_frame": 104,
            "candidate": "right_b_8",
            "schedule": [{"buttons": 0x82, "frames": 8}],
            "score": [1, 0, 40, 144],
        },
    )

    plan = v11._best_fresh_plan(responses, current_frame=108, freshness=8, last_applied_generation=-1)
    assert plan is not None
    assert plan["candidate"] == "right_b_8"
    assert plan["age"] == 4


def test_best_fresh_plan_rejects_stale_and_already_applied(tmp_path):
    responses = [tmp_path / "r0.json", tmp_path / "r1.json"]
    v11._atomic_json(
        responses[0],
        {
            "generation": 2,
            "root_frame": 90,
            "candidate": "right_b_8",
            "schedule": [{"buttons": 0x82, "frames": 8}],
            "score": [1, 0, 50, 140],
        },
    )
    v11._atomic_json(
        responses[1],
        {
            "generation": 3,
            "root_frame": 104,
            "candidate": "right_a_b_8",
            "schedule": [{"buttons": 0x83, "frames": 8}],
            "score": [1, 0, 30, 134],
        },
    )

    assert v11._best_fresh_plan(
        responses,
        current_frame=108,
        freshness=8,
        last_applied_generation=3,
    ) is None


def test_schedule_buttons_tracks_multi_command_macro():
    schedule = [
        {"buttons": 0x83, "frames": 6},
        {"buttons": 0x82, "frames": 24},
    ]
    assert v11._schedule_buttons(schedule, 0) == 0x83
    assert v11._schedule_buttons(schedule, 5) == 0x83
    assert v11._schedule_buttons(schedule, 6) == 0x82
    assert v11._schedule_buttons(schedule, 29) == 0x82
    assert v11._schedule_buttons(schedule, 40) == 0x82


def test_schedule_buttons_can_repeat_bootstrap_cycle():
    schedule = [
        {"buttons": 0x83, "frames": 8},
        {"buttons": 0x82, "frames": 8},
    ]
    assert v11._schedule_buttons(schedule, 0, repeat=True) == 0x83
    assert v11._schedule_buttons(schedule, 8, repeat=True) == 0x82
    assert v11._schedule_buttons(schedule, 16, repeat=True) == 0x83
    assert v11._schedule_buttons(schedule, 24, repeat=True) == 0x82
