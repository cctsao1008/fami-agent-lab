#!/usr/bin/env python3
"""V23 experimental live planner: receding-horizon Mesen forward-model control.

V22 solved lifecycle containment while retaining V20 gameplay semantics.  V23 is
the first live integration of issue #32's new authority contract:

- each shadow worker restores the newest exact Mesen checkpoint,
- a small auditable trajectory vocabulary is simulated to an event horizon,
- HORIZON is UNKNOWN, never proof of safety,
- DEATH is authoritative rejection,
- LANDING / capability change / reward collection / win are resolved outcomes,
- only a short control prefix is applied to live authority,
- the next control quantum re-observes and replans.

The branch result is deliberately used as a short-lived policy hint: asynchronous
compute means the simulated root can be a few live frames old.  V23 records that
source age and rebases only the selected *action prefix* onto current authority.
It does not claim that a stale branch is an exact proof of the current state.

V20 radar/reward/landing telemetry and V18 watchdog remain installed around the
new planner.  V22 Windows Job Object containment and checkpoint archiving are
preserved unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import (
    TrajectoryEvent,
    evaluate_mesen_trajectory,
    observation_from_state,
    read_smb1_state,
    trajectory_outcome_key,
)
from fami_pixel.games.smb1.forward_model import (
    BASELINE_TRAJECTORY_PLANS,
    execution_prefix_schedule,
    result_is_safe_resolved,
    shard_trajectory_plans,
)
from fami_pixel.runtime.checkpoint_archive import LiveCheckpointArchive
from fami_pixel.runtime.process_lifecycle import (
    WindowsKillOnCloseJob,
    authority_pid_from_env,
    isolated_run_dir,
    join_windows_job_from_env,
    leased_worker_environment,
    start_parent_lease_monitor,
    windows_job_environment,
)

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v17 as v17
import mesen_smb_checkpoint_planner_v18 as v18
import mesen_smb_checkpoint_planner_v20 as v20
import mesen_smb_checkpoint_planner_v21 as v21


PLANNER_NAME = "v23-forward-model-receding"
LIVE_TRAJECTORY_HORIZON = 64
EXECUTION_PREFIX_FRAMES = 4

_FORWARD_LABELS = {
    "fm_long_jump": "FM LONG JUMP: RIGHT+B 1f -> RIGHT+A+B",
    "fm_short_jump": "FM SHORT JUMP: RIGHT+B 1f -> RIGHT+A+B",
    "fm_brake_jump": "FM BRAKE JUMP: LEFT+B -> RELEASE -> RIGHT+B -> RIGHT+A+B",
    "fm_run": "FM RUN: RIGHT+B",
    "fm_coast": "FM COAST: RELEASE",
    "fm_backtrack": "FM BACKTRACK: LEFT+B",
}
_FORWARD_JUMP_NAMES = {"fm_long_jump", "fm_short_jump", "fm_brake_jump"}

_base_schedule_label = None
_base_append_timeline = None
_latest_forward_meta: dict = {}


def _worker_plans(worker_index: int, worker_count: int):
    plans = shard_trajectory_plans(worker_index, worker_count)
    # Keep response-path cohorts complete even if someone launches more workers
    # than the six baseline trajectories.  Extra workers duplicate one branch;
    # they never create a new action or alter ordering.
    if plans:
        return plans
    return (BASELINE_TRAJECTORY_PLANS[worker_index % len(BASELINE_TRAJECTORY_PLANS)],)


def _target_reward_from_request(req: dict) -> str | None:
    radar = dict(req.get("radar") or {})
    value = radar.get("nearest_reward_type")
    if value in {"mushroom", "fire_flower", "star", "one_up"}:
        return str(value)
    return None


def shadow_worker_main(args) -> int:
    """Evaluate exact Mesen futures for one deterministic trajectory shard."""

    assert args.request is not None and args.response is not None
    plans = _worker_plans(args.worker_index, args.worker_count)

    worker_home = Path(f"{args.shadow_home}-fm-{args.worker_index}")
    core = MesenCore(args.dll)
    core.initialize_headless(worker_home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        return 2
    core.initialize_debugger()

    last_generation = -1
    while True:
        req = v11._read_json(args.request)
        if req is None:
            time.sleep(0.001)
            continue
        generation = int(req.get("generation", -1))
        if generation <= last_generation:
            time.sleep(0.001)
            continue
        last_generation = generation

        checkpoint = Path(req["checkpoint"])
        root_frame = int(req["frame"])
        root_x = int(req["x"])
        root_engine = int(req["engine"])
        target_reward = _target_reward_from_request(req)
        started = time.perf_counter()
        best_result = None

        try:
            for plan in plans:
                base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
                start = observation_from_state(core.frame_count(), read_smb1_state(core))
                result = evaluate_mesen_trajectory(
                    core,
                    plan,
                    max_horizon_frames=LIVE_TRAJECTORY_HORIZON,
                    step_timeout_s=args.step_timeout,
                    target_reward_type=target_reward,
                    start_observation=start,
                    stop_on_landing=target_reward is None,
                )
                if best_result is None or trajectory_outcome_key(result) > trajectory_outcome_key(best_result):
                    best_result = result

            assert best_result is not None
            score = trajectory_outcome_key(best_result)
            event = best_result.event
            payload = {
                "generation": generation,
                "worker": args.worker_index,
                "root_frame": root_frame,
                "candidate": best_result.plan.name,
                # Execute only a short prefix. The selector rebases this schedule
                # to current authority time after checking source freshness.
                "schedule": execution_prefix_schedule(
                    best_result.plan,
                    EXECUTION_PREFIX_FRAMES,
                ),
                "score": list(score),
                "progress": int(best_result.progress),
                "terminal": "death" if event == TrajectoryEvent.DEATH else "none",
                "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "trajectory_event": event.value,
                "trajectory_safe_resolved": bool(result_is_safe_resolved(best_result)),
                "trajectory_frames": int(best_result.frames_simulated),
                "trajectory_end_x": int(best_result.end_x),
                "trajectory_max_x": int(best_result.max_x),
                "trajectory_end_y": int(best_result.end_y),
                "trajectory_landed": bool(best_result.landed),
                "trajectory_died": bool(best_result.died),
                "trajectory_reward_collected": bool(best_result.reward_collected),
                "trajectory_target_reward_type": best_result.target_reward_type,
                "trajectory_target_approach": best_result.target_approach,
                # Learned risk is not final authority in V23.  Keep these neutral
                # so legacy telemetry cannot veto a Mesen-resolved safe branch.
                "risk_probability": 0.0,
                "no_progress_probability": 0.0,
            }
            v11._atomic_json(args.response, payload)
        except Exception as exc:
            v11._atomic_json(
                args.response,
                {
                    "generation": generation,
                    "worker": args.worker_index,
                    "root_frame": root_frame,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def _spawn_forward_workers(args, request_path: Path, response_paths: list[Path]):
    workers: list[subprocess.Popen] = []
    env = leased_worker_environment(os.getpid())
    for index, response_path in enumerate(response_paths):
        cmd = [
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            str(args.rom),
            "--dll", str(args.dll),
            "--shadow-home", str(args.shadow_home),
            "--step-timeout", str(args.step_timeout),
            "--shadow-worker",
            "--worker-index", str(index),
            "--worker-count", str(len(response_paths)),
            "--request", str(request_path),
            "--response", str(response_path),
        ]
        if args.surrogate_model is not None:
            cmd.extend(["--surrogate-model", str(args.surrogate_model)])
        workers.append(
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        )
    v11._log("Shadow PIDs : " + ", ".join(str(worker.pid) for worker in workers))
    return workers


def _best_forward_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Select only a Mesen-resolved safe branch from one coherent cohort."""

    global _latest_forward_meta
    cohort = v14._freshest_complete_cohort(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
    )
    if cohort is None:
        _latest_forward_meta = {
            "forward_model_status": "waiting",
            "forward_model_event": None,
        }
        return None

    generation, source_root_frame, workers = cohort
    plans = [plan for plan in workers.values() if "error" not in plan]
    safe = [plan for plan in plans if bool(plan.get("trajectory_safe_resolved", False))]

    if not safe:
        events = sorted({str(plan.get("trajectory_event")) for plan in plans})
        _latest_forward_meta = {
            "forward_model_status": "no-safe-resolved-branch",
            "forward_model_generation": generation,
            "forward_model_source_frame": source_root_frame,
            "forward_model_source_age_frames": int(current_frame) - int(source_root_frame),
            "forward_model_events": events,
            "forward_model_candidates": len(plans),
        }
        # Returning None is intentional. V17 may inject its immediate live-radar
        # emergency jump for an observed hazard; otherwise current authority keeps
        # its previous short prefix until another coherent cohort arrives.
        return None

    best = max(safe, key=lambda plan: tuple(plan.get("score") or ()))
    result = dict(best)
    source_age = int(current_frame) - int(source_root_frame)

    # The exact Mesen result belongs to source_root_frame.  Async authority has
    # moved since then, so V23 uses the outcome as a policy hint and starts only
    # the short selected prefix *now*.  Preserve the source root separately for
    # audit instead of pretending that the old branch is current-state proof.
    result["trajectory_root_frame"] = int(source_root_frame)
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = int(current_frame)
    result["age"] = 0
    result["cohort_size"] = len(workers)
    result["cohort_generation"] = generation
    result["guard_mode"] = (
        "forward-model["
        f"{result.get('trajectory_event')},"
        f"src-age:{source_age}f,"
        f"sim:{result.get('trajectory_frames')}f]"
    )
    result["live_radar"] = dict(live_radar or {})

    _latest_forward_meta = {
        "forward_model_status": "selected",
        "forward_model_generation": generation,
        "forward_model_plan": result.get("candidate"),
        "forward_model_event": result.get("trajectory_event"),
        "forward_model_source_frame": source_root_frame,
        "forward_model_source_age_frames": source_age,
        "forward_model_simulated_frames": result.get("trajectory_frames"),
        "forward_model_progress": result.get("progress"),
        "forward_model_end_x": result.get("trajectory_end_x"),
        "forward_model_end_y": result.get("trajectory_end_y"),
        "forward_model_reward_collected": result.get("trajectory_reward_collected"),
        "forward_model_target_reward_type": result.get("trajectory_target_reward_type"),
        "forward_model_candidates": len(plans),
    }
    return result


def _forward_schedule_label(candidate_name: str) -> str:
    label = _FORWARD_LABELS.get(candidate_name)
    if label is not None:
        return f"{label} | execute {EXECUTION_PREFIX_FRAMES}f then replan"
    assert _base_schedule_label is not None
    return _base_schedule_label(candidate_name)


def _forward_append_timeline(recorder, payload: dict) -> None:
    assert _base_append_timeline is not None
    entry = dict(payload)
    entry.update(_latest_forward_meta)
    _base_append_timeline(recorder, entry)


def _install_forward_overrides() -> None:
    global _base_schedule_label, _base_append_timeline, _latest_forward_meta
    _latest_forward_meta = {}

    # V20 installs reward/landing/watchdog/radar/evidence layers first.  Replace
    # only the normal planner delegate and shadow evaluator; watchdog recovery
    # remains the outer preemption policy.
    _base_schedule_label = v14._schedule_label
    _base_append_timeline = v17._append_timeline

    v15._spawn_shadow_workers = _spawn_forward_workers
    v18._original_best_plan = _best_forward_plan
    v14._schedule_label = _forward_schedule_label
    v15._JUMP_NAMES.update(_FORWARD_JUMP_NAMES)
    v17._append_timeline = _forward_append_timeline
    v17.PLANNER_NAME = PLANNER_NAME


def authority_main(args) -> int:
    if os.name == "nt":
        try:
            if join_windows_job_from_env():
                v11._log("Process job : authority joined supervisor kill-on-close job")
            else:
                v11._log("Process job : no supervisor job supplied; using Python cleanup only")
        except OSError as exc:
            v11._log(f"Process job : join failed ({exc}); using fallback cleanup")

    if args.surrogate_model is None:
        raise SystemExit("V23 requires --surrogate-model <trained JSON artifact> for legacy telemetry/fallback compatibility")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    runtime_dir = isolated_run_dir(args.checkpoint_dir)
    args.checkpoint_dir = runtime_dir
    args.shadow_home = args.shadow_home.expanduser().resolve() / runtime_dir.name

    # Install the complete V20 observation/watchdog/evidence stack, then swap in
    # the V23 Mesen trajectory evaluator as the normal-plan delegate.
    v20._install_landing_overrides()
    _install_forward_overrides()

    v11._log(f"Runtime IPC : {runtime_dir}")
    v11._log(
        "Planner V23: live Mesen forward model enabled | "
        f"horizon={LIVE_TRAJECTORY_HORIZON}f prefix={EXECUTION_PREFIX_FRAMES}f "
        "HORIZON=UNKNOWN; safe resolved branches only"
    )
    v11._log(
        "Forward model: Mesen outcomes are final branch authority; "
        "async source age is recorded and only the short action prefix is rebased live"
    )
    return v17.authority_main(args)


def _runtime_dir_from_line(line: str) -> Path | None:
    marker = "Runtime IPC :"
    if marker not in line:
        return None
    text = line.split(marker, 1)[1].strip()
    return Path(text).expanduser().resolve() if text else None


def supervise_main() -> int:
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--authority-worker",
    ]

    job = None
    env = os.environ.copy()
    if os.name == "nt":
        try:
            job = WindowsKillOnCloseJob.create()
            if job is not None:
                env = windows_job_environment(job.name, base=env)
                v11._log(f"Process job : {job.name} | KILL_ON_JOB_CLOSE")
        except OSError as exc:
            v11._log(f"Process job : creation failed ({exc}); using fallback cleanup")
            job = None

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None

    shadow_pids: set[int] = set()
    result_code: int | None = None
    checkpoint_archive: LiveCheckpointArchive | None = None

    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            shadow_pids.update(v21._parse_shadow_pids(line))

            if checkpoint_archive is None:
                runtime_dir = _runtime_dir_from_line(line)
                if runtime_dir is not None:
                    checkpoint_archive = LiveCheckpointArchive(runtime_dir)
                    checkpoint_archive.start()
                    v11._log(
                        f"Checkpoint archive: {checkpoint_archive.archive_dir} | preserving live roots"
                    )

            payload = line[line.find("PlannerV11:"):] if "PlannerV11:" in line else line
            if payload.strip().startswith("PlannerV11: FAIL watchdog stall"):
                code = 8
            else:
                code = v11._terminal_code(payload)
            if code is None:
                continue

            result_code = code
            try:
                proc.wait(timeout=v11._SUPERVISOR_GRACE_S)
            except subprocess.TimeoutExpired:
                v11._log(
                    "V23 supervisor: terminal result observed; authority still alive after grace"
                )
            break

        if result_code is None:
            result_code = proc.wait()
        return int(result_code)
    except KeyboardInterrupt:
        v11._log("V23 supervisor: Ctrl+C received; closing current run process group")
        return 130
    finally:
        if job is not None:
            v11._log("V23 supervisor: closing kill-on-close process job")
            job.close()

        if proc.poll() is None:
            v11._terminate_process_tree(proc)

        if checkpoint_archive is not None:
            checkpoint_archive.stop()
            v11._log(
                "Checkpoint archive: complete | "
                f"preserved={checkpoint_archive.archived_count}"
            )

        v21._scrub_shadow_pids(shadow_pids)


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        parent_pid = authority_pid_from_env()
        start_parent_lease_monitor(parent_pid)
        return shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
