#!/usr/bin/env python3
"""V19 live planner: reward-aware radar and safe power-up pursuit.

V18 can see hazards, recover from control livelock, and persist run evidence, but
it treats positive objects as irrelevant to planning. V19 adds an explicit
reward channel without weakening the existing authority boundary:

- PowerUpObject is excluded from hostile-enemy radar semantics.
- Current authoritative radar exposes Mushroom / Fire Flower / Star / 1-Up.
- Reward value depends on Mario capability state and distance.
- Reward preference is considered only inside immediate-Mesen-safe and
  learned-risk-bounded candidates.
- Active Star invincibility reduces ordinary enemy avoidance pressure while
  gaps/terrain remain hazards.
- Web UI and timeline evidence record reward target, utility, pursuit state,
  capability state, and observable collection transitions.

Mesen remains authoritative for world state, collision, collection, death, level
completion, and framebuffer evidence.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from fami_pixel.games.smb1.radar import read_smb1_radar as _native_read_smb1_radar
from fami_pixel.games.smb1.rewards import (
    RewardOpportunity,
    best_reward_opportunity,
    select_reward_preferred_plan,
    should_pursue_reward,
)
from fami_pixel.telemetry import format_radar_strip

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v12 as v12
import mesen_smb_checkpoint_planner_v13 as v13
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v16 as v16
import mesen_smb_checkpoint_planner_v17 as v17
import mesen_smb_checkpoint_planner_v18 as v18


PLANNER_NAME = "v19-reward-aware-radar"

# Capture the V16 live-radar planner before V18 installs its watchdog wrapper.
_BASE_LIVE_PLAN = v16.best_coherent_live_radar_plan

_latest_radar_payload: dict = {}
_watchdog_append_timeline = None
_watchdog_publish_core = None
_previous_reward_type: str | None = None
_previous_player_status: int | None = None
_previous_star_timer: int | None = None


def _reset_reward_runtime_state() -> None:
    global _latest_radar_payload
    global _previous_reward_type, _previous_player_status, _previous_star_timer
    _latest_radar_payload = {}
    _previous_reward_type = None
    _previous_player_status = None
    _previous_star_timer = None


def _reward_aware_radar_reason(radar: dict) -> str | None:
    """Return current hazards while respecting authoritative Star capability."""
    enemy = radar.get("nearest_enemy_dx")
    gap = radar.get("nearest_gap_dx")
    obstacle = radar.get("nearest_obstacle_dx")
    invincible = bool(radar.get("invincible", False))

    reasons: list[str] = []
    if (
        not invincible
        and enemy is not None
        and 0 <= int(enemy) <= v15.RADAR_ENEMY_TRIGGER_PX
    ):
        reasons.append(f"enemy:{int(enemy)}")
    if gap is not None and 0 <= int(gap) <= v15.RADAR_GAP_TRIGGER_PX:
        reasons.append(f"gap:{int(gap)}")
    if obstacle is not None and 0 <= int(obstacle) <= v15.RADAR_OBSTACLE_TRIGGER_PX:
        reasons.append(f"obstacle:{int(obstacle)}")
    return ",".join(reasons) if reasons else None


def _annotate_reward_result(
    result: dict | None,
    opportunity: RewardOpportunity | None,
    *,
    pursuit_mode: str,
) -> dict | None:
    if result is None:
        return None
    enriched = dict(result)
    enriched["pursuit_mode"] = pursuit_mode
    enriched["reward_target_type"] = None if opportunity is None else opportunity.reward_type
    enriched["reward_target_dx"] = None if opportunity is None else opportunity.dx
    enriched["reward_utility"] = None if opportunity is None else float(opportunity.utility)
    return enriched


def best_coherent_reward_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Prefer a bounded reward route only after current hazard/safety checks."""
    hazard_reason = _reward_aware_radar_reason(live_radar)
    opportunity = should_pursue_reward(live_radar)

    # A current near-field hazard keeps V16's hazard-first behavior. Reward
    # pursuit never competes with immediate scene safety.
    if hazard_reason is not None:
        base = _BASE_LIVE_PLAN(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
            live_radar,
        )
        return _annotate_reward_result(base, opportunity, pursuit_mode="hazard-first")

    if opportunity is not None:
        cohort = v14._freshest_complete_cohort(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
        )
        if cohort is not None:
            generation, root_frame, workers = cohort
            plans = list(workers.values())
            selected, selected_opportunity = select_reward_preferred_plan(
                plans,
                live_radar,
                risk_cutoff=v14._RISK_CUTOFF,
            )
            if selected is not None and selected_opportunity is not None:
                result = dict(selected)
                result["age"] = int(current_frame) - int(root_frame)
                result["cohort_size"] = len(workers)
                result["cohort_generation"] = generation
                result["guarded_utility"] = v13._guarded_score(selected)[2]
                result["guard_mode"] = (
                    "reward-pursuit["
                    f"{selected_opportunity.reward_type}:{selected_opportunity.dx},"
                    f"u:{selected_opportunity.utility:.2f}]"
                )
                result["risk_cutoff"] = v14._RISK_CUTOFF
                result["live_radar"] = dict(live_radar)
                return _annotate_reward_result(
                    result,
                    selected_opportunity,
                    pursuit_mode="pursuit",
                )

    # No safely actionable reward: preserve normal live-radar + risk behavior.
    base = _BASE_LIVE_PLAN(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )
    mode = "observe" if best_reward_opportunity(live_radar) is not None else "none"
    return _annotate_reward_result(base, opportunity, pursuit_mode=mode)


def _tracking_read_smb1_radar(core, *, player_x: int, lookahead_px: int = 192):
    global _latest_radar_payload
    snapshot = _native_read_smb1_radar(
        core,
        player_x=player_x,
        lookahead_px=lookahead_px,
    )
    _latest_radar_payload = snapshot.to_payload()
    return snapshot


def _reward_collection_transition(radar: dict) -> str | None:
    """Detect only capability changes that SMB1 state can authoritatively prove."""
    global _previous_reward_type, _previous_player_status, _previous_star_timer

    current_type = radar.get("nearest_reward_type")
    player_status = int(radar.get("player_status", 0))
    star_timer = int(radar.get("star_invincible_timer", 0))
    collected = None

    if _previous_reward_type in {"mushroom", "fire_flower"}:
        if _previous_player_status is not None and player_status > _previous_player_status:
            collected = _previous_reward_type
    elif _previous_reward_type == "star":
        if _previous_star_timer is not None and star_timer > _previous_star_timer:
            collected = "star"

    _previous_reward_type = None if current_type is None else str(current_type)
    _previous_player_status = player_status
    _previous_star_timer = star_timer
    return collected


def _reward_append_timeline(recorder, payload: dict) -> None:
    assert _watchdog_append_timeline is not None
    entry = dict(payload)
    radar = dict(entry.get("radar") or {})
    opportunity = best_reward_opportunity(radar)
    guard_mode = str(entry.get("guard_mode") or "")
    collected = _reward_collection_transition(radar)

    entry.update(
        {
            "reward_target_type": None if opportunity is None else opportunity.reward_type,
            "reward_target_dx": None if opportunity is None else opportunity.dx,
            "reward_utility": None if opportunity is None else float(opportunity.utility),
            "pursuit_mode": "pursuit" if guard_mode.startswith("reward-pursuit[") else (
                "observe" if opportunity is not None else "none"
            ),
            "player_status": radar.get("player_status"),
            "star_invincible_timer": radar.get("star_invincible_timer"),
            "invincible": bool(radar.get("invincible", False)),
            "reward_collected_type": collected,
        }
    )
    _watchdog_append_timeline(recorder, entry)


def _reward_publish_core(
    self,
    core,
    observation,
    *,
    decision: int,
    mode: str,
    action: str,
    metadata: dict | None = None,
):
    assert _watchdog_publish_core is not None
    details = dict(metadata or {})
    radar = dict(_latest_radar_payload)
    opportunity = best_reward_opportunity(radar)
    guard_mode = str(details.get("guard_mode") or "")
    pursuit_mode = "pursuit" if guard_mode.startswith("reward-pursuit[") else (
        "observe" if opportunity is not None else "none"
    )
    reward_type = radar.get("nearest_reward_type")
    marker = {
        "star": "S",
        "mushroom": "M",
        "fire_flower": "F",
        "one_up": "1",
    }.get(str(reward_type), "R")

    details.update(
        {
            "radar_enemy_dx": radar.get("nearest_enemy_dx"),
            "radar_gap_dx": radar.get("nearest_gap_dx"),
            "radar_obstacle_dx": radar.get("nearest_obstacle_dx"),
            "radar_reward_dx": radar.get("nearest_reward_dx"),
            "radar_reward_type": reward_type,
            "reward_utility": None if opportunity is None else float(opportunity.utility),
            "pursuit_mode": pursuit_mode,
            "player_status": radar.get("player_status"),
            "star_invincible_timer": radar.get("star_invincible_timer"),
            "radar_strip": format_radar_strip(
                radar.get("nearest_enemy_dx"),
                radar.get("nearest_gap_dx"),
                radar.get("nearest_obstacle_dx"),
                reward_dx=radar.get("nearest_reward_dx"),
                reward_marker=marker,
                lookahead_px=max(1, int(radar.get("lookahead_px", v15.RADAR_LOOKAHEAD_PX))),
            ),
            "hazard_ahead": _reward_aware_radar_reason(radar) is not None,
        }
    )
    return _watchdog_publish_core(
        self,
        core,
        observation,
        decision=decision,
        mode="REWARD RADAR + WATCHDOG",
        action=action,
        metadata=details,
    )


def _install_reward_overrides() -> None:
    global _watchdog_append_timeline, _watchdog_publish_core

    _reset_reward_runtime_state()
    v18._install_watchdog_overrides()

    # Keep V18 watchdog as the outer control policy. Its normal-plan delegate is
    # replaced with V19 reward-aware selection; watchdog recovery/abort still
    # preempts it exactly as before.
    v18._original_best_plan = best_coherent_reward_plan
    v15._radar_reason = _reward_aware_radar_reason
    v17.read_smb1_radar = _tracking_read_smb1_radar
    v17.PLANNER_NAME = PLANNER_NAME

    _watchdog_append_timeline = v17._append_timeline
    v17._append_timeline = _reward_append_timeline

    _watchdog_publish_core = v17.NesWebViewer.publish_core
    v17.NesWebViewer.publish_core = _reward_publish_core


def authority_main(args) -> int:
    _install_reward_overrides()
    v11._log(
        "Planner V19: reward-aware native radar enabled | "
        "hazard-first + state-dependent power-up pursuit + V18 watchdog"
    )
    return v17.authority_main(args)


def supervise_main() -> int:
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--authority-worker",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            payload = line[line.find("PlannerV11:"):] if "PlannerV11:" in line else line
            if payload.strip().startswith("PlannerV11: FAIL watchdog stall"):
                code = 8
            else:
                code = v11._terminal_code(payload)
            if code is None:
                continue
            try:
                proc.wait(timeout=v11._SUPERVISOR_GRACE_S)
            except subprocess.TimeoutExpired:
                v11._log("V19 supervisor: terminal result observed; terminating process tree")
                v11._terminate_process_tree(proc)
            return code
        return proc.wait()
    except KeyboardInterrupt:
        v11._log("V19 supervisor: Ctrl+C received; terminating authority + shadow process tree")
        v11._terminate_process_tree(proc)
        return 130


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        return v15.shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
