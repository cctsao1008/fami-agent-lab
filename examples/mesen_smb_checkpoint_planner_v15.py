#!/usr/bin/env python3
"""V15 live planner: native forward-scene radar + learned-risk guard.

V14 can re-arm A and avoid high learned-risk actions, but it still decides from
candidate outcomes alone. V15 adds a generation-aligned native scene sensor:
active SMB1 enemy slots plus the game's own rolling collision block buffers.

The radar is read from each shadow root after restoring the exact checkpoint,
then copied into the worker response. The coherent selector therefore reasons
about the same scene that produced the candidate rollouts. When a near enemy,
gap, or raised obstacle is ahead, a Mesen-safe jump-rearm candidate is preferred
instead of blindly continuing to run.

Mesen remains authoritative for transitions and terminal events.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import observation_from_state, read_smb1_state
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.learning.tiny_mlp import TinySurrogateMLP

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v10 as v10
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v12 as v12
import mesen_smb_checkpoint_planner_v13 as v13
import mesen_smb_checkpoint_planner_v14 as v14


RADAR_LOOKAHEAD_PX = 192
RADAR_ENEMY_TRIGGER_PX = 96
RADAR_GAP_TRIGGER_PX = 80
RADAR_OBSTACLE_TRIGGER_PX = 64
_JUMP_NAMES = {"rearm_jump_8", "rearm_jump_12"}


def _candidate_shard(worker_index: int, worker_count: int):
    candidates = v14._candidate_pool()
    return tuple(
        candidate
        for index, candidate in enumerate(candidates)
        if index % worker_count == worker_index
    )


def shadow_worker_main(args) -> int:
    """Evaluate V14 actions and attach a native radar snapshot to each root."""
    assert args.request is not None and args.response is not None
    candidates = _candidate_shard(args.worker_index, args.worker_count)
    if not candidates:
        return 3

    surrogate = None
    if args.surrogate_model is not None:
        surrogate = TinySurrogateMLP.load_json(args.surrogate_model)

    worker_home = Path(f"{args.shadow_home}-{args.worker_index}")
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
        best = None
        best_outcome = None
        best_score = None
        best_prediction = None
        radar_payload = None
        started = time.perf_counter()

        try:
            for candidate in candidates:
                base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
                start = observation_from_state(core.frame_count(), read_smb1_state(core))
                if radar_payload is None:
                    radar_payload = read_smb1_radar(
                        core,
                        player_x=start.mario_x_abs,
                        lookahead_px=RADAR_LOOKAHEAD_PX,
                    ).to_payload()
                prediction = (
                    surrogate.predict(v11._surrogate_record(start, candidate))
                    if surrogate is not None
                    else None
                )
                outcome = v10.run_candidate_pit_aware(core, candidate, start, args.step_timeout)
                score = v11._score_outcome(outcome)
                if best_score is None or score > best_score:
                    best_score = score
                    best = candidate
                    best_outcome = outcome
                    best_prediction = prediction

            if best is not None and best_outcome is not None:
                payload = {
                    "generation": generation,
                    "worker": args.worker_index,
                    "root_frame": root_frame,
                    "candidate": best.name,
                    "schedule": v11._schedule_payload(best),
                    "score": list(best_score),
                    "progress": best_outcome.progress,
                    "terminal": best_outcome.terminal.value,
                    "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "radar": radar_payload or {},
                }
                if best_prediction is not None:
                    payload.update(
                        {
                            "surrogate_delta_x": float(best_prediction["delta_x"]),
                            "risk_probability": float(best_prediction["risk_probability"]),
                            "no_progress_probability": float(best_prediction["no_progress_probability"]),
                        }
                    )
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


def _spawn_shadow_workers(args, request_path: Path, response_paths: list[Path]):
    workers: list[subprocess.Popen] = []
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
            )
        )
    return workers


def _radar_reason(radar: dict) -> str | None:
    enemy = radar.get("nearest_enemy_dx")
    gap = radar.get("nearest_gap_dx")
    obstacle = radar.get("nearest_obstacle_dx")
    reasons = []
    if enemy is not None and 0 <= int(enemy) <= RADAR_ENEMY_TRIGGER_PX:
        reasons.append(f"enemy:{int(enemy)}")
    if gap is not None and 0 <= int(gap) <= RADAR_GAP_TRIGGER_PX:
        reasons.append(f"gap:{int(gap)}")
    if obstacle is not None and 0 <= int(obstacle) <= RADAR_OBSTACLE_TRIGGER_PX:
        reasons.append(f"obstacle:{int(obstacle)}")
    return ",".join(reasons) if reasons else None


def _eligible_with_risk_gate(plans: list[dict]) -> tuple[list[dict], str]:
    immediate_safe = [
        plan
        for plan in plans
        if str(plan.get("terminal", "none")) != "death"
        and (not plan.get("score") or float(plan["score"][0]) >= 0.0)
    ]
    candidates = immediate_safe or plans
    under_cutoff = [
        plan
        for plan in candidates
        if float(plan.get("risk_probability", 1.0)) <= v14._RISK_CUTOFF
    ]
    if under_cutoff:
        return under_cutoff, "below-cutoff"
    minimum = min(
        float(plan.get("risk_probability", 1.0))
        for plan in candidates
    )
    return (
        [
            plan
            for plan in candidates
            if float(plan.get("risk_probability", 1.0)) == minimum
        ],
        "min-risk-fallback",
    )


def best_coherent_radar_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
):
    """Scene-aware selection over one coherent V14 candidate cohort."""
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
    eligible, risk_mode = _eligible_with_risk_gate(plans)

    radar = dict(plans[0].get("radar") or {})
    reason = _radar_reason(radar)
    jump_candidates = [
        plan for plan in eligible if str(plan.get("candidate")) in _JUMP_NAMES
    ]

    if reason is not None and jump_candidates:
        best = max(jump_candidates, key=v13._guarded_score)
        guard_mode = f"radar-jump[{reason}]"
    else:
        best = max(eligible, key=v13._guarded_score)
        if reason is not None:
            guard_mode = f"radar-hazard-fallback[{reason}]/{risk_mode}"
        else:
            guard_mode = f"radar-clear/{risk_mode}"

    result = dict(best)
    result["age"] = current_frame - root_frame
    result["cohort_size"] = len(workers)
    result["cohort_generation"] = generation
    result["guarded_utility"] = v13._guarded_score(best)[2]
    result["guard_mode"] = guard_mode
    result["risk_cutoff"] = v14._RISK_CUTOFF
    result["radar"] = radar
    return result


def _install_v15_overrides() -> None:
    v11._candidate_pool = v14._candidate_pool
    v11._schedule_label = v14._schedule_label
    v11._spawn_shadow_workers = _spawn_shadow_workers
    v11._best_fresh_plan = best_coherent_radar_plan


def authority_main(args) -> int:
    if args.surrogate_model is None:
        raise SystemExit("V15 requires --surrogate-model <trained JSON artifact>")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    v14._RISK_CUTOFF = float(args.surrogate_risk_cutoff)
    v13._RISK_PENALTY = float(args.surrogate_risk_penalty)
    v13._STALL_PENALTY = float(args.surrogate_no_progress_penalty)
    v13._DX_WEIGHT = float(args.surrogate_dx_weight)
    v12.reset_response_cache()
    _install_v15_overrides()

    v11._log(
        "Planner V15: native forward radar + jump re-arm + learned-risk gate | "
        f"lookahead={RADAR_LOOKAHEAD_PX}px enemy={RADAR_ENEMY_TRIGGER_PX}px "
        f"gap={RADAR_GAP_TRIGGER_PX}px obstacle={RADAR_OBSTACLE_TRIGGER_PX}px "
        f"risk_cutoff={v14._RISK_CUTOFF:g}"
    )
    v11._log(f"Surrogate : {model_path}")
    return v11.authority_main(args)


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
            v11._log("V15 supervisor: terminal result observed; terminating process tree")
            v11._terminate_process_tree(proc)
        return code

    return proc.wait()


def main() -> int:
    args = v11.parse_args()
    _install_v15_overrides()
    if args.shadow_worker:
        return shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
