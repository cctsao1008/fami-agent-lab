from fami_pixel.games.smb1.rewards import (
    best_reward_opportunity,
    select_reward_preferred_plan,
    state_dependent_reward_value,
)


def _plan(name: str, *, progress: int, risk: float = 0.05, terminal: str = "none") -> dict:
    return {
        "candidate": name,
        "terminal": terminal,
        "score": [1 if terminal != "death" else -1, 0, progress, progress],
        "progress": progress,
        "risk_probability": risk,
    }


def test_reward_value_depends_on_player_capability():
    assert state_dependent_reward_value(
        "mushroom", player_status=0, star_invincible_timer=0
    ) > state_dependent_reward_value(
        "mushroom", player_status=1, star_invincible_timer=0
    )
    assert state_dependent_reward_value(
        "fire_flower", player_status=1, star_invincible_timer=0
    ) > state_dependent_reward_value(
        "fire_flower", player_status=2, star_invincible_timer=0
    )
    assert state_dependent_reward_value(
        "star", player_status=0, star_invincible_timer=0
    ) > state_dependent_reward_value(
        "star", player_status=0, star_invincible_timer=20
    )


def test_best_reward_opportunity_prefers_high_value_near_target():
    radar = {
        "lookahead_px": 192,
        "player_status": 0,
        "star_invincible_timer": 0,
        "rewards": [
            {"slot": 5, "type": "mushroom", "dx": 32, "y": 176},
            {"slot": 5, "type": "star", "dx": 64, "y": 144},
        ],
    }

    opportunity = best_reward_opportunity(radar)

    assert opportunity is not None
    assert opportunity.reward_type == "star"
    assert opportunity.utility > 0.0


def test_safely_reachable_star_changes_preference_to_jump():
    radar = {
        "lookahead_px": 192,
        "player_status": 0,
        "star_invincible_timer": 0,
        "rewards": [
            {"slot": 5, "type": "star", "dx": 56, "y": 144},
        ],
    }
    plans = [
        _plan("run_8", progress=14, risk=0.02),
        _plan("rearm_jump_8", progress=10, risk=0.04),
        _plan("rearm_jump_12", progress=12, risk=0.05),
        _plan("right_8", progress=8, risk=0.01),
    ]

    selected, opportunity = select_reward_preferred_plan(
        plans,
        radar,
        risk_cutoff=0.20,
    )

    assert opportunity is not None
    assert selected is not None
    assert selected["candidate"] == "rearm_jump_12"


def test_reward_never_resurrects_immediate_mesen_death_candidate():
    radar = {
        "lookahead_px": 192,
        "player_status": 0,
        "star_invincible_timer": 0,
        "rewards": [
            {"slot": 5, "type": "star", "dx": 40, "y": 144},
        ],
    }
    plans = [
        _plan("rearm_jump_12", progress=30, risk=0.01, terminal="death"),
        _plan("rearm_jump_8", progress=8, risk=0.05),
        _plan("run_8", progress=12, risk=0.05),
    ]

    selected, _ = select_reward_preferred_plan(plans, radar, risk_cutoff=0.20)

    assert selected is not None
    assert selected["candidate"] == "rearm_jump_8"
    assert selected["terminal"] != "death"
