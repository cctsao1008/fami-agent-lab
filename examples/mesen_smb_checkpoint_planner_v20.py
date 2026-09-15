#!/usr/bin/env python3
"""V20 live planner: enemy-cluster awareness and landing-zone safety.

V19 can identify hazards and rewards, but a nearest-enemy trigger is not enough
for dense scenes. A running jump can clear the first Goomba and still descend
into a second cluster. V20 adds a conservative trajectory projection over the
current authoritative radar:

- all forward hostile enemy slots are grouped into clusters,
- an evidence-tunable +96..+160 px landing corridor is checked for occupancy,
- a grounded unsafe landing corridor triggers a longer re-armed jump,
- an airborne unsafe landing corridor keeps A+B held for a bounded extension,
- reward pursuit is preempted while the landing corridor is enemy-occupied,
- V19 reward semantics, V18 watchdog, evidence capture, and leased workers stay
  intact.

This is deliberately a heuristic landing envelope, not a replacement physics
model. Mesen remains authoritative for the actual trajectory and collision.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from fami_pixel.games.smb1 import ActionCommand, PlanCandidate, Smb1Action, read_smb1_state
from fami_pixel.games.smb1.landing import assess_landing_zone
from fami_pixel.runtime.process_lifecycle import (
    authority_pid_from_env,
    isolated_run_dir,
    start_parent_lease_monitor,
)

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v16 as v16
import mesen_smb_checkpoint_planner_v17 as v17
import mesen_smb_checkpoint_planner_v18 as v18
import mesen_smb_checkpoint_planner_v19 as v19


PLANNER_NAME = "v20-landing-zone-radar"

CLUSTER_JUMP = PlanCandidate(
    "cluster_jump_16",
    (
        ActionCommand(Smb1Action.RIGHT_B, 1),
        ActionCommand(Smb1Action.RIGHT_A_B, 15),
    ),
)
CLUSTER_EXTEND = PlanCandidate(
    "cluster_extend_8",
    (ActionCommand(Smb1Action.RIGHT_A_B, 8),),
)

_BASE_REWARD_PLAN = v19.best_coherent_reward_plan
_base_append_timeline = None
_base_publish_core = None
_base_schedule_label = None
_latest_landing_payload: dict = {}


def _grounded_from_state(state) -> bool:
    vy = v11._signed_u8(state.player_y_speed)
    return int(state.player_y_high) == 1 and int(state.player_y) >= 160 and vy == 0


class _LandingRadarProxy:
    def __init__(self, snapshot, state):
        self._snapshot = snapshot
        self._state = state

    def to_payload(self) -> dict:
        global _latest_landing_payload
        payload = self._snapshot.to_payload()
        payload.update(
            {
                "player_y": int(self._state.player_y),
                "player_y_high": int(self._state.player_y_high),
                "player_vy": int(v11._signed_u8(self._state.player_y_speed)),
                "grounded": _grounded_from_state(self._state),
            }
        )
        assessment = assess_landing_zone(payload)
        landing = assessment.to_payload()
        payload.update(landing)
        _latest_landing_payload = landing
        # V19's reward UI/timeline wrapper reads this shared latest-radar view.
        v19._latest_radar_payload = dict(payload)
        return payload


def _tracking_landing_radar(core, *, player_x: int, lookahead_px: int = 192):
    snapshot = v19._native_read_smb1_radar(
        core,
        player_x=player_x,
        lookahead_px=lookahead_px,
    )
    return _LandingRadarProxy(snapshot, read_smb1_state(core))


def _landing_reason(radar: dict) -> str:
    count = int(radar.get("forward_enemy_count", 0) or 0)
    cluster = int(radar.get("nearest_cluster_count", 0) or 0)
    start = radar.get("nearest_cluster_start_dx")
    end = radar.get("nearest_cluster_end_dx")
    landing_count = int(radar.get("landing_enemy_count", 0) or 0)
    corridor_start = int(radar.get("landing_corridor_start_dx", 96) or 96)
    corridor_end = int(radar.get("landing_corridor_end_dx", 160) or 160)
    return (
        f"enemies:{count},cluster:{cluster}@{start}..{end},"
        f"landing:{landing_count}@{corridor_start}..{corridor_end}"
    )


def _scene_plan(candidate: PlanCandidate, current_frame: int, radar: dict, *, mode: str) -> dict:
    return {
        "generation": -1,
        "worker": "landing-reactive",
        "root_frame": int(current_frame),
        "candidate": candidate.name,
        "schedule": v11._schedule_payload(candidate),
        "score": [1, 0, 0, 0],
        "progress": 0,
        "terminal": "none",
        "age": 0,
        "compute_ms": 0.0,
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
        "guard_mode": f"landing-zone-{mode}[{_landing_reason(radar)}]",
        "live_radar": dict(radar),
        "landing_safe": False,
    }


def best_coherent_landing_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Preempt reward/progress planning only when the landing envelope is occupied."""
    assessment = assess_landing_zone(live_radar)
    radar = dict(live_radar)
    radar.update(assessment.to_payload())

    # Star capability makes ordinary enemy contact non-fatal; gaps/terrain are
    # still handled by the V19/V16 hazard path below.
    if assessment.landing_unsafe and not bool(radar.get("invincible", False)):
        if bool(radar.get("grounded", False)):
            return _scene_plan(CLUSTER_JUMP, current_frame, radar, mode="escape")
        return _scene_plan(CLUSTER_EXTEND, current_frame, radar, mode="extend")

    return _BASE_REWARD_PLAN(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        radar,
    )


def _landing_schedule_label(candidate_name: str) -> str:
    if candidate_name == CLUSTER_JUMP.name:
        return "LANDING ESCAPE: RIGHT+B 1f -> RIGHT+A+B 15f"
    if candidate_name == CLUSTER_EXTEND.name:
        return "LANDING EXTEND: RIGHT+A+B 8f"
    assert _base_schedule_label is not None
    return _base_schedule_label(candidate_name)


def _landing_append_timeline(recorder, payload: dict) -> None:
    assert _base_append_timeline is not None
    entry = dict(payload)
    radar = dict(entry.get("radar") or {})
    landing = assess_landing_zone(radar).to_payload()
    entry.update(landing)
    _base_append_timeline(recorder, entry)


def _landing_publish_core(
    self,
    core,
    observation,
    *,
    decision: int,
    mode: str,
    action: str,
    metadata: dict | None = None,
):
    assert _base_publish_core is not None
    details = dict(metadata or {})
    radar = dict(v19._latest_radar_payload or {})
    landing = assess_landing_zone(radar).to_payload()
    details["planner_state"] = (
        f"{details.get('planner_state') or 'live-radar'} | "
        f"cluster={landing['nearest_cluster_count']} "
        f"landing={'SAFE' if landing['landing_safe'] else 'UNSAFE'}"
    )
    existing_reason = details.get("radar_reason")
    landing_note = _landing_reason({**radar, **landing})
    details["radar_reason"] = (
        landing_note if not existing_reason else f"{existing_reason} | {landing_note}"
    )
    return _base_publish_core(
        self,
        core,
        observation,
        decision=decision,
        mode="LANDING RADAR + REWARD + WATCHDOG",
        action=action,
        metadata=details,
    )


def _install_landing_overrides() -> None:
    global _base_append_timeline, _base_publish_core, _base_schedule_label

    # Build V19 first so reward semantics, watchdog, leased workers, and evidence
    # wrappers are already installed. Then make landing safety the next outer
    # scene-driven policy layer.
    v19._install_reward_overrides()
    v17.PLANNER_NAME = PLANNER_NAME
    v17.read_smb1_radar = _tracking_landing_radar
    v18._original_best_plan = best_coherent_landing_plan

    _base_append_timeline = v17._append_timeline
    v17._append_timeline = _landing_append_timeline

    _base_publish_core = v17.NesWebViewer.publish_core
    v17.NesWebViewer.publish_core = _landing_publish_core

    _base_schedule_label = v14._schedule_label
    v14._schedule_label = _landing_schedule_label
    v15._JUMP_NAMES.update({CLUSTER_JUMP.name, CLUSTER_EXTEND.name})


def authority_main(args) -> int:
    # Preserve #29's per-run IPC isolation for V20 as well.
    runtime_dir = isolated_run_dir(args.checkpoint_dir)
    args.checkpoint_dir = runtime_dir
    args.shadow_home = args.shadow_home.expanduser().resolve() / runtime_dir.name

    _install_landing_overrides()
    v11._log(f"Runtime IPC : {runtime_dir}")
    v11._log(
        "Planner V20: enemy-cluster + landing-zone safety enabled | "
        "corridor=96..160px + extended cluster jump + V19 reward/watchdog"
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
                v11._log("V20 supervisor: terminal result observed; terminating process tree")
                v11._terminate_process_tree(proc)
            return code
        return proc.wait()
    except KeyboardInterrupt:
        v11._log("V20 supervisor: Ctrl+C received; terminating authority + shadow process tree")
        v11._terminate_process_tree(proc)
        return 130


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        parent_pid = authority_pid_from_env()
        start_parent_lease_monitor(parent_pid)
        return v15.shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
