#!/usr/bin/env python3
"""V14 live planner: jump re-arm actions plus hard learned-risk gating.

V13 showed the learned risk head rising before the first World 1-1 death, but
still selected the same held-A action until impact. Two live-control defects are
addressed here without changing the Mesen authority boundary:

1. Repeating RIGHT+A+B does not create a fresh A press after A is already held.
   V14 therefore gives the live cohort explicit jump-rearm macros: release A for
   one frame with RIGHT+B, then press RIGHT+A+B.
2. Learned risk is no longer only a soft utility penalty. If at least one
   immediate-Mesen-safe candidate is below the configured risk cutoff, candidates
   above the cutoff are excluded. If every immediate-safe candidate is above the
   cutoff, the lowest-risk candidate wins before progress is considered.

All four candidates are still replayed by independent shadow Mesen processes.
The learned surrogate remains a proposal/risk guard; authoritative death/level
completion still comes only from the live Mesen trajectory.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import subprocess
import sys

from fami_pixel.games.smb1 import ActionCommand, PlanCandidate, Smb1Action

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v12 as v12
import mesen_smb_checkpoint_planner_v13 as v13


_RISK_CUTOFF = 0.20


def _candidate_pool() -> tuple[PlanCandidate, ...]:
    """Four latency-bounded live actions, including two explicit A re-arms."""
    return (
        PlanCandidate("run_8", (ActionCommand(Smb1Action.RIGHT_B, 8),)),
        PlanCandidate(
            "rearm_jump_8",
            (
                ActionCommand(Smb1Action.RIGHT_B, 1),
                ActionCommand(Smb1Action.RIGHT_A_B, 7),
            ),
        ),
        PlanCandidate(
            "rearm_jump_12",
            (
                ActionCommand(Smb1Action.RIGHT_B, 1),
                ActionCommand(Smb1Action.RIGHT_A_B, 11),
            ),
        ),
        PlanCandidate("right_8", (ActionCommand(Smb1Action.RIGHT, 8),)),
    )


def _schedule_label(candidate_name: str) -> str:
    labels = {
        "run_8": "RIGHT+B 8f",
        "rearm_jump_8": "RIGHT+B 1f -> RIGHT+A+B 7f",
        "rearm_jump_12": "RIGHT+B 1f -> RIGHT+A+B 11f",
        "right_8": "RIGHT 8f",
    }
    return labels.get(candidate_name, candidate_name)


def _spawn_shadow_workers(args, request_path: Path, response_paths: list[Path]):
    """Spawn this V14 script so workers inherit the V14 candidate pool."""
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


def _freshest_complete_cohort(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
):
    expected_workers = set(range(len(response_paths)))

    for path in response_paths:
        response = v11._read_json(path)
        if response is None or "error" in response:
            continue
        generation = int(response.get("generation", -1))
        worker = int(response.get("worker", -1))
        root_frame = int(response.get("root_frame", -1))
        if generation < 0 or worker not in expected_workers or root_frame < 0:
            continue
        v12._response_cache[(generation, worker)] = dict(response)

    for key, response in list(v12._response_cache.items()):
        generation, _worker = key
        root_frame = int(response.get("root_frame", -1))
        age = current_frame - root_frame
        if generation <= last_applied_generation or age > freshness:
            del v12._response_cache[key]

    cohorts: dict[tuple[int, int], dict[int, dict]] = defaultdict(dict)
    for (generation, worker), response in v12._response_cache.items():
        root_frame = int(response["root_frame"])
        age = current_frame - root_frame
        if generation <= last_applied_generation or age < 0 or age > freshness:
            continue
        cohorts[(generation, root_frame)][worker] = response

    complete: list[tuple[int, int, dict[int, dict]]] = []
    for (generation, root_frame), workers in cohorts.items():
        if set(workers) == expected_workers:
            complete.append((generation, root_frame, workers))
    if not complete:
        return None
    return max(complete, key=lambda item: (item[1], item[0]))


def best_coherent_hard_risk_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
):
    """Choose a Mesen-safe candidate with a hard delayed-risk guard."""
    cohort = _freshest_complete_cohort(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
    )
    if cohort is None:
        return None

    generation, root_frame, workers = cohort
    plans = list(workers.values())
    immediate_safe = [
        plan for plan in plans
        if str(plan.get("terminal", "none")) != "death"
        and (not plan.get("score") or float(plan["score"][0]) >= 0.0)
    ]
    candidates = immediate_safe or plans

    under_cutoff = [
        plan for plan in candidates
        if float(plan.get("risk_probability", 1.0)) <= _RISK_CUTOFF
    ]
    if under_cutoff:
        eligible = under_cutoff
        guard_mode = "below-cutoff"
        best = max(eligible, key=v13._guarded_score)
    else:
        # When every option looks dangerous, survival dominates progress.
        guard_mode = "min-risk-fallback"
        best = min(
            candidates,
            key=lambda plan: (
                float(plan.get("risk_probability", 1.0)),
                float(plan.get("no_progress_probability", 1.0)),
                -v13._guarded_score(plan)[2],
            ),
        )

    result = dict(best)
    result["age"] = current_frame - root_frame
    result["cohort_size"] = len(workers)
    result["cohort_generation"] = generation
    result["guarded_utility"] = v13._guarded_score(best)[2]
    result["guard_mode"] = guard_mode
    result["risk_cutoff"] = _RISK_CUTOFF
    return result


def _install_v14_overrides() -> None:
    v11._candidate_pool = _candidate_pool
    v11._schedule_label = _schedule_label
    v11._spawn_shadow_workers = _spawn_shadow_workers
    v11._best_fresh_plan = best_coherent_hard_risk_plan


def authority_main(args) -> int:
    global _RISK_CUTOFF

    if args.surrogate_model is None:
        raise SystemExit("V14 requires --surrogate-model <trained JSON artifact>")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    _RISK_CUTOFF = float(args.surrogate_risk_cutoff)
    v13._RISK_PENALTY = float(args.surrogate_risk_penalty)
    v13._STALL_PENALTY = float(args.surrogate_no_progress_penalty)
    v13._DX_WEIGHT = float(args.surrogate_dx_weight)
    v12.reset_response_cache()
    _install_v14_overrides()

    v11._log(
        "Planner V14: jump re-arm + hard learned-risk gate enabled | "
        f"risk_cutoff={_RISK_CUTOFF:g} risk_penalty={v13._RISK_PENALTY:g} "
        f"stall_penalty={v13._STALL_PENALTY:g} dx_weight={v13._DX_WEIGHT:g}"
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
            v11._log("V14 supervisor: terminal result observed; terminating process tree")
            v11._terminate_process_tree(proc)
        return code

    return proc.wait()


def main() -> int:
    args = v11.parse_args()
    _install_v14_overrides()
    if args.shadow_worker:
        return v11.shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
