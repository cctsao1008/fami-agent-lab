#!/usr/bin/env python3
"""V17 live planner: observable live radar plus durable terminal evidence.

V16 closes the current-authority perception/action loop. V17 keeps those control
semantics unchanged and adds observability around them:

- the Web UI receives the live-authority radar, guard mode, and surrogate scores,
- every control quantum is appended to a local JSONL timeline,
- death, level completion, and frame-limit exits persist a final authoritative
  PNG plus structured state/radar/summary JSON under ``build/live-runs``.

Artifact capture is best-effort and never changes the authoritative terminal
result. Mesen remains the source of truth for transitions, framebuffer, and game
termination.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller, copy_nes_raw_frame
from fami_pixel.games.smb1 import GameEventType, derive_game_events, observation_from_state, read_smb1_state
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.telemetry import LiveRunArtifacts, NesWebViewer, format_radar_strip

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v12 as v12
import mesen_smb_checkpoint_planner_v13 as v13
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v16 as v16


UI_STRIDE = 2
PLANNER_NAME = "v17-live-radar-evidence"


def _terminal_radar(core, current, fallback: dict) -> dict:
    try:
        return read_smb1_radar(
            core,
            player_x=current.mario_x_abs,
            lookahead_px=v15.RADAR_LOOKAHEAD_PX,
        ).to_payload()
    except Exception:
        return dict(fallback or {})


def _create_recorder() -> LiveRunArtifacts | None:
    try:
        recorder = LiveRunArtifacts(root=Path("build/live-runs"), planner=PLANNER_NAME)
        v11._log(f"Run evidence : {recorder.path}")
        return recorder
    except Exception as exc:
        v11._log(f"Run evidence disabled: {type(exc).__name__}: {exc}")
        return None


def _append_timeline(recorder: LiveRunArtifacts | None, payload: dict) -> None:
    if recorder is None:
        return
    try:
        recorder.append_timeline(payload)
    except Exception as exc:
        v11._log(f"Timeline write skipped: {type(exc).__name__}: {exc}")


def _persist_terminal(
    recorder: LiveRunArtifacts | None,
    *,
    terminal: str,
    core,
    current,
    live_radar_payload: dict,
    args,
    model_path: Path,
    applied_label: str,
    last_plan_root,
    last_plan_age,
    last_plan_compute_ms,
    last_guard_mode,
    last_risk,
    last_stall,
) -> None:
    """Best-effort final evidence write; never mask the authoritative result."""
    if recorder is None:
        return
    try:
        radar = _terminal_radar(core, current, live_radar_payload)
        frame = copy_nes_raw_frame(core)
        path = recorder.finalize(
            terminal=terminal,
            frame=frame,
            observation=current,
            radar=radar,
            summary={
                "planner": PLANNER_NAME,
                "rom": str(args.rom.expanduser().resolve()),
                "surrogate_model": str(model_path),
                "shadow_workers": int(args.shadow_workers),
                "control_quantum_frames": int(args.control_quantum),
                "plan_freshness_frames": int(args.plan_freshness),
                "risk_cutoff": float(v14._RISK_CUTOFF),
                "final_action": applied_label,
                "final_plan_root_frame": last_plan_root,
                "final_plan_age_frames": last_plan_age,
                "final_plan_compute_ms": last_plan_compute_ms,
                "final_guard_mode": last_guard_mode,
                "final_risk_probability": last_risk,
                "final_no_progress_probability": last_stall,
            },
        )
        v11._log(f"Run artifacts: {path}")
    except Exception as exc:
        v11._log(f"Run artifact write failed: {type(exc).__name__}: {exc}")


def authority_main(args) -> int:
    if args.surrogate_model is None:
        raise SystemExit("V17 requires --surrogate-model <trained JSON artifact>")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    v14._RISK_CUTOFF = float(args.surrogate_risk_cutoff)
    v13._RISK_PENALTY = float(args.surrogate_risk_penalty)
    v13._STALL_PENALTY = float(args.surrogate_no_progress_penalty)
    v13._DX_WEIGHT = float(args.surrogate_dx_weight)
    v12.reset_response_cache()

    recorder = _create_recorder()

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
        "Planner V17: live radar + reactive jump + observable evidence | "
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
    last_guard_mode = "bootstrap"
    last_risk = None
    last_stall = None
    live_radar_payload: dict = {}
    radar_reason = None

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
                    mode="LIVE RADAR + EVIDENCE",
                    action=applied_label,
                    metadata={
                        "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                        "plan_age": last_plan_age,
                        "planner_state": f"live-radar/parallel-{args.shadow_workers}",
                        "plan_compute_ms": last_plan_compute_ms,
                        "guard_mode": last_guard_mode,
                        "risk_probability": last_risk,
                        "no_progress_probability": last_stall,
                        "radar_enemy_dx": live_radar_payload.get("nearest_enemy_dx"),
                        "radar_gap_dx": live_radar_payload.get("nearest_gap_dx"),
                        "radar_obstacle_dx": live_radar_payload.get("nearest_obstacle_dx"),
                        "radar_reason": radar_reason,
                        "radar_strip": format_radar_strip(
                            live_radar_payload.get("nearest_enemy_dx"),
                            live_radar_payload.get("nearest_gap_dx"),
                            live_radar_payload.get("nearest_obstacle_dx"),
                            lookahead_px=v15.RADAR_LOOKAHEAD_PX,
                        ),
                        "hazard_ahead": radar_reason is not None,
                    },
                )

            if any(e.kind == GameEventType.DIED for e in events):
                _persist_terminal(
                    recorder,
                    terminal="death",
                    core=core,
                    current=current,
                    live_radar_payload=live_radar_payload,
                    args=args,
                    model_path=model_path,
                    applied_label=applied_label,
                    last_plan_root=last_plan_root,
                    last_plan_age=last_plan_age,
                    last_plan_compute_ms=last_plan_compute_ms,
                    last_guard_mode=last_guard_mode,
                    last_risk=last_risk,
                    last_stall=last_stall,
                )
                v11._log(f"PlannerV11: FAIL death | frame={current.native_frame_id} X={current.mario_x_abs}")
                return 6
            if any(e.kind == GameEventType.LEVEL_COMPLETED for e in events):
                _persist_terminal(
                    recorder,
                    terminal="level_complete",
                    core=core,
                    current=current,
                    live_radar_payload=live_radar_payload,
                    args=args,
                    model_path=model_path,
                    applied_label=applied_label,
                    last_plan_root=last_plan_root,
                    last_plan_age=last_plan_age,
                    last_plan_compute_ms=last_plan_compute_ms,
                    last_guard_mode=last_guard_mode,
                    last_risk=last_risk,
                    last_stall=last_stall,
                )
                v11._log(f"PlannerV11: PASS level complete | frame={current.native_frame_id} X={current.mario_x_abs}")
                return 0

            if loop_index % args.control_quantum == 0:
                live_radar_payload = read_smb1_radar(
                    core,
                    player_x=current.mario_x_abs,
                    lookahead_px=v15.RADAR_LOOKAHEAD_PX,
                ).to_payload()
                radar_reason = v15._radar_reason(live_radar_payload)

                plan = v16.best_coherent_live_radar_plan(
                    response_paths,
                    current.native_frame_id,
                    args.plan_freshness,
                    last_applied_generation,
                    live_radar_payload,
                )

                if radar_reason is not None and v16._looks_grounded(current):
                    if plan is None or str(plan.get("candidate")) not in v15._JUMP_NAMES:
                        plan = v16._emergency_jump_plan(current.native_frame_id, live_radar_payload)

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
                    last_guard_mode = str(plan.get("guard_mode", "none"))
                    last_risk = float(plan.get("risk_probability", 0.0))
                    last_stall = float(plan.get("no_progress_probability", 0.0))

                    enemy = live_radar_payload.get("nearest_enemy_dx")
                    gap = live_radar_payload.get("nearest_gap_dx")
                    obstacle = live_radar_payload.get("nearest_obstacle_dx")
                    v11._log(
                        f"control update: {applied_label} root={last_plan_root} "
                        f"age={last_plan_age}f worker={plan.get('worker')} "
                        f"compute={last_plan_compute_ms:.1f}ms "
                        f"radar=e:{enemy}/g:{gap}/o:{obstacle} "
                        f"risk={last_risk:.3f} guard={last_guard_mode}"
                    )

                _append_timeline(
                    recorder,
                    {
                        "generation": generation,
                        "native_frame": int(current.native_frame_id),
                        "mario_x": int(current.mario_x_abs),
                        "mario_y": int(current.mario_y),
                        "action": applied_label,
                        "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                        "plan_age_frames": last_plan_age,
                        "plan_compute_ms": last_plan_compute_ms,
                        "guard_mode": last_guard_mode,
                        "risk_probability": last_risk,
                        "no_progress_probability": last_stall,
                        "radar_reason": radar_reason,
                        "radar": live_radar_payload,
                    },
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

        _persist_terminal(
            recorder,
            terminal="frame_limit",
            core=core,
            current=current,
            live_radar_payload=live_radar_payload,
            args=args,
            model_path=model_path,
            applied_label=applied_label,
            last_plan_root=last_plan_root,
            last_plan_age=last_plan_age,
            last_plan_compute_ms=last_plan_compute_ms,
            last_guard_mode=last_guard_mode,
            last_risk=last_risk,
            last_stall=last_stall,
        )
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
            v11._log("V17 supervisor: terminal result observed; terminating process tree")
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
