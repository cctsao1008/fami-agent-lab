#!/usr/bin/env python3
"""V13 learned-risk guard over the V12 coherent live planner.

V12 fixes cohort coherence but still ranks the four short-horizon Mesen outcomes
only by immediate rollout progress.  That is blind to the delayed-death pattern
measured by the offline teacher.  V13 keeps Mesen authoritative and uses the
trained TinySurrogateMLP only as a soft guard when choosing among one coherent
four-worker cohort.

The learned model never emits authoritative game events.  Each candidate is
still replayed by a shadow Mesen instance; the model contributes a risk/stall
penalty to the final proposal score.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import subprocess
import sys

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v12 as v12


_RISK_PENALTY = 24.0
_STALL_PENALTY = 8.0
_DX_WEIGHT = 0.25


def _guarded_score(plan: dict) -> tuple[float, ...]:
    score = list(plan.get("score", []))
    immediate_safe = float(score[0]) if len(score) > 0 else 0.0
    flagpole = float(score[1]) if len(score) > 1 else 0.0
    progress = float(plan.get("progress", score[2] if len(score) > 2 else 0.0))
    max_x = float(score[3]) if len(score) > 3 else progress
    predicted_dx = float(plan.get("surrogate_delta_x", progress))
    risk = float(plan.get("risk_probability", 1.0))
    stall = float(plan.get("no_progress_probability", 1.0))
    utility = progress + (_DX_WEIGHT * predicted_dx) - (_RISK_PENALTY * risk) - (_STALL_PENALTY * stall)
    return (immediate_safe, flagpole, utility, progress, max_x)


def best_coherent_risk_guarded_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
):
    """Choose the best risk-guarded plan from the freshest complete cohort."""
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

    generation, root_frame, workers = max(complete, key=lambda item: (item[1], item[0]))
    best = max(workers.values(), key=_guarded_score)
    result = dict(best)
    result["age"] = current_frame - root_frame
    result["cohort_size"] = len(workers)
    result["cohort_generation"] = generation
    result["guarded_utility"] = _guarded_score(best)[2]
    return result


def authority_main(args) -> int:
    global _RISK_PENALTY, _STALL_PENALTY, _DX_WEIGHT

    if args.surrogate_model is None:
        raise SystemExit("V13 requires --surrogate-model <trained JSON artifact>")
    model_path = args.surrogate_model.expanduser().resolve()
    if not model_path.is_file():
        raise SystemExit(f"surrogate model not found: {model_path}")
    args.surrogate_model = model_path

    _RISK_PENALTY = float(args.surrogate_risk_penalty)
    _STALL_PENALTY = float(args.surrogate_no_progress_penalty)
    _DX_WEIGHT = float(args.surrogate_dx_weight)
    v12.reset_response_cache()
    v11._best_fresh_plan = best_coherent_risk_guarded_plan
    v11._log(
        "Planner V13: coherent learned-risk guard enabled | "
        f"risk_penalty={_RISK_PENALTY:g} stall_penalty={_STALL_PENALTY:g} "
        f"dx_weight={_DX_WEIGHT:g}"
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
            v11._log("V13 supervisor: terminal result observed; terminating process tree")
            v11._terminate_process_tree(proc)
        return code

    return proc.wait()


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        return v11.shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
