from fami_pixel.adapters.mesen import NES_B, NES_LEFT, NES_RIGHT
from fami_pixel.games.smb1.reward_beam import REWARD_BEAM_CHUNKS_WITH_HOLD
from fami_pixel.games.smb1.reward_live import (
    StickyCollectObjective,
    reward_chunk_schedule,
    select_fresh_reward_response,
)


def _radar(*, status=0, star_timer=0):
    return {"player_status": status, "star_invincible_timer": star_timer}


def test_sticky_collect_objective_survives_short_target_dropout():
    objective = StickyCollectObjective(ttl_frames=8)
    first = objective.update(
        frame=100,
        radar=_radar(),
        tracked_reward={"type": "star", "dx": -1},
    )
    assert first["mode"] == "COLLECT"
    assert first["target_type"] == "star"
    assert first["sticky"] is False

    missing = objective.update(frame=104, radar=_radar(), tracked_reward=None)
    assert missing["mode"] == "COLLECT"
    assert missing["target_type"] == "star"
    assert missing["sticky"] is True

    expired = objective.update(frame=109, radar=_radar(), tracked_reward=None)
    assert expired["mode"] == "PROGRESS"
    assert expired["target_type"] is None


def test_sticky_collect_objective_ends_on_authoritative_star_timer_change():
    objective = StickyCollectObjective()
    objective.update(
        frame=200,
        radar=_radar(star_timer=0),
        tracked_reward={"type": "star", "dx": 4},
    )
    result = objective.update(
        frame=204,
        radar=_radar(star_timer=35),
        tracked_reward=None,
    )
    assert result["mode"] == "PROGRESS"
    assert result["collected_type"] == "star"
    assert objective.target_type is None


def test_reward_chunk_schedule_releases_a_after_proven_prefix():
    chunks = {chunk.name: chunk for chunk in REWARD_BEAM_CHUNKS_WITH_HOLD}
    left = reward_chunk_schedule(chunks["hold_left_jump4"])
    right = reward_chunk_schedule(chunks["hold_right_jump4"])

    assert left[0]["frames"] == 4
    assert left[-1] == {"buttons": NES_LEFT | NES_B, "frames": 1}
    assert right[0]["frames"] == 4
    assert right[-1] == {"buttons": NES_RIGHT | NES_B, "frames": 1}


def test_reward_response_selector_prefers_newest_generation_then_reward_key():
    responses = [
        {
            "generation": 7,
            "root_frame": 100,
            "planner_mode": "collect",
            "target_reward_type": "star",
            "reward_prefix_safe": True,
            "reward_key": [1, 1, 0],
            "candidate": "old",
        },
        {
            "generation": 8,
            "root_frame": 104,
            "planner_mode": "collect",
            "target_reward_type": "star",
            "reward_prefix_safe": True,
            "reward_key": [1, 0, 9],
            "candidate": "new-weaker",
        },
        {
            "generation": 8,
            "root_frame": 104,
            "planner_mode": "collect",
            "target_reward_type": "star",
            "reward_prefix_safe": True,
            "reward_key": [1, 1, 2],
            "candidate": "new-best",
        },
    ]
    selected = select_fresh_reward_response(
        responses,
        current_frame=108,
        freshness=16,
        last_applied_generation=6,
        target_type="star",
    )
    assert selected is not None
    assert selected["candidate"] == "new-best"
    assert selected["age"] == 4


def test_reward_response_selector_rejects_stale_or_unsafe_prefixes():
    responses = [
        {
            "generation": 9,
            "root_frame": 80,
            "planner_mode": "collect",
            "target_reward_type": "star",
            "reward_prefix_safe": True,
            "reward_key": [9],
        },
        {
            "generation": 10,
            "root_frame": 108,
            "planner_mode": "collect",
            "target_reward_type": "star",
            "reward_prefix_safe": False,
            "reward_key": [99],
        },
    ]
    assert (
        select_fresh_reward_response(
            responses,
            current_frame=112,
            freshness=16,
            last_applied_generation=8,
            target_type="star",
        )
        is None
    )
