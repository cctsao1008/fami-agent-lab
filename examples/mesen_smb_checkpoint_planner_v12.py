#!/usr/bin/env python3
"""V12 coherent-generation aggregation for the V11 live controller.

V11 proved continuous authority and deadline-feasible shadow rollouts, but its
selector compared whatever single worker result had the newest root frame. With
one candidate per worker, that meant the controller often applied the fastest
worker's result instead of comparing all four candidates from the same state.

V12 keeps the V11 execution topology and fixes only that aggregation contract:
- cache each worker response as it is observed,
- group responses by (generation, root_frame),
- apply a plan only when the full worker cohort for that generation is present,
- rank candidates only within that coherent cohort,
- never wait for a cohort; authority keeps running and may skip generations.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import subprocess
import sys

import mesen_smb_checkpoint_planner_v11 as v11


_response_cache: dict[tuple[int, int], dict] = {}


def reset_response_cache() -> None:
    _response_cache.clear()


def best_coherent_fresh_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
):
    """Return the best candidate from the newest complete fresh generation.

    Response files use latest-value semantics and can be overwritten at
    different times by workers with different rollout costs. Cache every result
    observed by authority so a slower worker can complete a cohort after faster
    workers have already advanced to a newer generation.
    """
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
        _response_cache[(generation, worker)] = dict(response)

    # Bound the cache by both controller history and the frame freshness window.
    for key, response in list(_response_cache.items()):
        generation, _worker = key
        root_frame = int(response.get("root_frame", -1))
        age = current_frame - root_frame
        if generation <= last_applied_generation or age > freshness:
            del _response_cache[key]

    cohorts: dict[tuple[int, int], dict[int, dict]] = defaultdict(dict)
    for (generation, worker), response in _response_cache.items():
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

    # Prefer the freshest complete state, not the newest single worker result.
    generation, root_frame, workers = max(
        complete,
        key=lambda item: (item[1], item[0]),
    )
    best = max(workers.values(), key=lambda p: tuple(p.get("score", [])))
    result = dict(best)
    result["age"] = current_frame - root_frame
    result["cohort_size"] = len(workers)
    result["cohort_generation"] = generation
    return result


def authority_main(args) -> int:
    reset_response_cache()
    v11._best_fresh_plan = best_coherent_fresh_plan
    v11._log(
        f"Planner V12: coherent {args.shadow_workers}-worker generation aggregation enabled"
    )
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
            v11._log(
                "V12 supervisor: terminal result observed; terminating authority + shadow process tree"
            )
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
