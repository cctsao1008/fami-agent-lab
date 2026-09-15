#!/usr/bin/env python3
"""V16 live planner: current-authority radar closes the perception/action loop.

V15 proved that the native radar can see the first World 1-1 enemy, but the
selector consumed the radar snapshot attached to an 8-16 frame old shadow root.
It also applied the learned-risk gate before asking whether the live scene
required a jump, so an out-of-distribution risk estimate for the two-command
jump-rearm macros could remove both jump actions before the radar got a vote.

V16 moves perception back onto the authoritative trajectory.  Every control
quantum the live Mesen instance reads the current SMB1 radar before choosing an
action.  A current near-field hazard makes scene evidence dominate the learned
prior: among immediate-Mesen-safe actions, jump-rearm candidates are preferred.
If no coherent jump result is available yet and Mario appears grounded, a short
reactive jump-rearm macro is issued directly while shadow planning catches up.

The learned model remains a proposal/risk prior. Mesen remains authoritative for
transitions and terminal events.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import GameEventType, derive_game_events, observation_from_state, read_smb1_state
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.telemetry import NesWebViewer

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v12 as v12
import mesen_smb_checkpoint_planner_v13 as v13
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v15 as v15


UI_STRIDE = 2


def _immediate_safe(plans: list[dict]) -> list[dict]:
    safe = [
        plan
        for plan in plans
        if str(plan.get("terminal", "none")) != "death"
        and (not plan.get("score") or float(plan["score"][0]) >= 0.0)
    ]
    return safe or plans


def _mesen_progress_score(plan: dict) -> tuple[float, ...]:
    score = list(plan.get("score", []))
    safe = float(score[0]) if len(score) > 0 else 0.0
    flagpole = float(score[1]) if len(score) > 1 else 0.0
    progress = float(plan.get("progress", score[2] if len(score) > 2 else 0.0))
    max_x = float(score[3]) if len(score) > 3 else progress
    return (safe, flagpole, progress, max_x)


def best_coherent_live_radar_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Select using the current authoritative scene, not the old shadow scene."""
    cohort = v14._freshest_complete_cohort(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
    )
    if cohort is None:
        return None

    generation, root_frame, workers = cohort
    plans = list(workers.values())
    safe = _immediate_safe(plans)
    reason = v15._radar_reason(live_radar)

    if reason is not None:
        # Scene evidence is current; the learned risk for these two-command
        # jump-rearm macros is extrapolative because the current training set is
        # dominated by one-command precision candidates.  Do not let that prior
        # delete both evasive actions before radar can use them.
        jump_candidates = [
            plan for plan in safe if str(plan.get("candidate")) in v15._JUMP_NAMES
        ]
        if jump_candidates:
            best = max(jump_candidates, key=_mesen_progress_score)
            guard_mode = f"live-radar-jump[{reason}]"
        else:
            eligible, risk_mode = v15._eligible_with_risk_gate(safe)
            best = max(eligible, key=v13._guarded_score)
            guard_mode = f"live-radar-no-jump[{reason}]/{risk_mode}"
    else:
        eligible, risk_mode = v15._eligible_with_risk_gate(safe)
        best = max(eligible, key=v13._guarded_score)
        guard_mode = f"live-radar-clear/{risk_mode}"

    result = dict(best)
    result["age"] = current_frame - root_frame
    result["cohort_size"] = len(workers)
    result["cohort_generation"] = generation
    result["guarded_utility"] = v13._guarded_score(best)[2]
    result["guard_mode"] = guard_mode
    result["risk_cutoff"] = v14._RISK_CUTOFF
    result["live_radar"] = dict(live_radar)
    return result


def _looks_grounded(observation) -> bool:
    vy = v11._signed_u8(observation.player_y_speed)
    return int(observation.mario_y_high) == 1 and int(observation.mario_y) >= 160 and vy == 0


def _emergency_jump_plan(current_frame: int, radar: dict) -> dict:
    candidate = next(
        candidate for candidate in v14._candidate_pool() if candidate.name == "rearm_jump_12"
    )
    return {
        "generation": -1,
        "worker": "reactive",
        "root_frame": int(current_frame),
        "candidate": candidate.name,
        "schedule": v11._schedule_payload(candidate),
        "age": 0,
        "compute_ms": 0.0,
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
        "guard_mode": f"live-radar-emergency[{v15._radar_reason(radar) or 'hazard'}]",
        "live_radar": dict(radar),
    }


def authority_main(args) -> int:
    if args.surrogate_model is None:
        raise SystemExit("V16 requires --surrogate-model <trained JSON artifact>")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    v14._RISK_CUTOFF = float(args.surrogate_risk_cutoff)
    v13._RISK_PENALTY = float(args.surrogate_risk_penalty)
    v13._STALL_PENALTY = float(args.surrogate_no_progress_penalty)
    v13._DX_WEIGHT = float(args.surrogate_dx_weight)
    v12.reset_response_cache()

    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    request_path = checkpoint_dir / "request.json"
    response_paths = [checkpoint_dir / f"response-{i}.json" for i in range(args.shadow_workers)]
    for stale in [request_path, *response_paths]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    workers = v15._spawn_shadow_workers(args, request_path, response_paths)

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        for worker in workers:
            worker.terminate()
        return 2
    core.initialize_debugger()
    state = base.enter_world_1_1(core, args.step_timeout)
    current = observation_from_state(core.frame_count(), state)
    previous = current

    viewer = None
    if args.web_ui:
        viewer = NesWebViewer(port=args.web_port, playback_fps=30.0)
        viewer.start()
        v11._log(f"Web UI    : {viewer.url}")

    v11._log(
        "Planner V16: current-authority radar + reactive jump + shadow Mesen | "
        f"control={args.control_quantum}f freshness={args.plan_freshness}f "
        f"enemy={v15.RADAR_ENEMY_TRIGGER_PX}px gap={v15.RADAR_GAP_TRIGGER_PX}px "
        f"obstacle={v15.RADAR_OBSTACLE_TRIGGER_PX}px risk_cutoff={v14._RISK_CUTOFF:g}"
    )
    v11._log(f"Surrogate : {model_path}")

    applied_schedule = v11.BOOTSTRAP_SCHEDULE
    applied_label = "BOOTSTRAP PULSE-JUMP"
    applied_plan_root = current.native_frame_id
    using_bootstrap = True
    generation = 0
    last_applied_generation = -1
    last_plan_root = -1
    last_plan_age = None
    last_plan_compute_ms = None
    live_radar_payload: dict = {}

    try:
        for loop_index in range(args.max_frames):
            schedule_age = max(0, current.native_frame_id - applied_plan_root)
            applied_buttons = v11._schedule_buttons(
                applied_schedule,
                schedule_age,
                repeat=using_bootstrap,
            )

            base.set_nes_controller_state(core, 0, applied_buttons)
            base.step(core, args.step_timeout)
            state = read_smb1_state(core)
            current = observation_from_state(core.frame_count(), state)
            events = derive_game_events(previous, current)

            if viewer is not None and loop_index % UI_STRIDE == 0:
                viewer.publish_core(
                    core,
                    current,
                    decision=generation,
                    mode="LIVE RADAR RHC",
                    action=applied_label,
                    metadata={
                        "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                        "plan_age": last_plan_age,
                        "planner_state": f"live-radar/parallel-{args.shadow_workers}",
                        "plan_compute_ms": last_plan_compute_ms,
                        "radar_enemy_dx": live_radar_payload.get("nearest_enemy_dx"),
                        "radar_gap_dx": live_radar_payload.get("nearest_gap_dx"),
                        "radar_obstacle_dx": live_radar_payload.get("nearest_obstacle_dx"),
                    },
                )

            if any(e.kind == GameEventType.DIED for e in events):
                v11._log(f"PlannerV11: FAIL death | frame={current.native_frame_id} X={current.mario_x_abs}")
                return 6
            if any(e.kind == GameEventType.LEVEL_COMPLETED for e in events):
                v11._log(f"PlannerV11: PASS level complete | frame={current.native_frame_id} X={current.mario_x_abs}")
                return 0

            if loop_index % args.control_quantum == 0:
                live_radar_payload = read_smb1_radar(
                    core,
                    player_x=current.mario_x_abs,
                    lookahead_px=v15.RADAR_LOOKAHEAD_PX,
                ).to_payload()
                reason = v15._radar_reason(live_radar_payload)

                plan = best_coherent_live_radar_plan(
                    response_paths,
                    current.native_frame_id,
                    args.plan_freshness,
                    last_applied_generation,
                    live_radar_payload,
                )

                # A current near-field hazard must not wait for a stale/full
                # shadow cohort.  If Mario is grounded and no jump result is
                # ready, issue the same bounded re-arm macro reactively.
                if reason is not None and _looks_grounded(current):
                    if plan is None or str(plan.get("candidate")) not in v15._JUMP_NAMES:
                        plan = _emergency_jump_plan(current.native_frame_id, live_radar_payload)

                if plan is not None:
                    applied_schedule = list(plan.get("schedule") or [])
                    applied_label = v14._schedule_label(str(plan["candidate"]))
                    applied_plan_root = int(plan["root_frame"])
                    using_bootstrap = False
                    plan_generation = int(plan.get("generation", -1))
                    if plan_generation >= 0:
                        last_applied_generation = plan_generation
                    last_plan_root = applied_plan_root
                    last_plan_age = int(plan.get("age", 0))
                    last_plan_compute_ms = float(plan.get("compute_ms", 0.0))
                    enemy = live_radar_payload.get("nearest_enemy_dx")
                    gap = live_radar_payload.get("nearest_gap_dx")
                    obstacle = live_radar_payload.get("nearest_obstacle_dx")
                    v11._log(
                        f"control update: {applied_label} root={last_plan_root} "
                        f"age={last_plan_age}f worker={plan.get('worker')} "
                        f"compute={last_plan_compute_ms:.1f}ms "
                        f"radar=e:{enemy}/g:{gap}/o:{obstacle} "
                        f"risk={float(plan.get('risk_probability', 0.0)):.3f} "
                        f"guard={plan.get('guard_mode', 'none')}"
                    )

                generation += 1
                checkpoint = checkpoint_dir / f"live-{generation:06d}.mss"
                frame, x, engine = base.save_checkpoint(core, checkpoint)
                published = v11._atomic_json(
                    request_path,
                    {
                        "generation": generation,
                        "checkpoint": str(checkpoint),
                        "frame": frame,
                        "x": x,
                        "engine": engine,
                        "radar": live_radar_payload,
                    },
                )
                if not published:
                    v11._log(
                        f"IPC backpressure: dropped planner snapshot generation={generation} "
                        "after transient Windows sharing conflicts"
                    )

                keep = max(6, (args.plan_freshness // args.control_quantum) + 6)
                obsolete_generation = generation - keep
                if obsolete_generation > 0:
                    try:
                        (checkpoint_dir / f"live-{obsolete_generation:06d}.mss").unlink()
                    except FileNotFoundError:
                        pass

            previous = current

        v11._log(f"PlannerV11: FAIL frame limit | frame={current.native_frame_id} X={current.mario_x_abs}")
        return 7
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
        for worker in workers:
            try:
                worker.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                worker.kill()
        try:
            base.set_nes_controller_state(core, 0, 0x00)
        except Exception:
            pass
        if viewer is not None:
            try:
                viewer.stop()
            except Exception:
                pass


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

    for line in iter(proc.stdout.readline, ""):
        print(line, end="", flush=True)
        payload = line[line.find("PlannerV11:"):] if "PlannerV11:" in line else line
        code = v11._terminal_code(payload)
        if code is None:
            continue
        try:
            proc.wait(timeout=v11._SUPERVISOR_GRACE_S)
        except subprocess.TimeoutExpired:
            v11._log("V16 supervisor: terminal result observed; terminating process tree")
            v11._terminate_process_tree(proc)
        return code

    return proc.wait()


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        return v15.shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
