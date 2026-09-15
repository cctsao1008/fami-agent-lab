#!/usr/bin/env python3
"""V24 live planner: latency-tolerant partial forward-model control.

V23 field evidence exposed two integration bugs rather than a bad Mesen model:

1. the selector waited for a complete six-worker cohort, so one slow HORIZON
   branch blocked already-resolved jump branches until they became stale;
2. each 4-frame execution prefix ended in RELEASE, and V11 holds the final
   schedule segment when no replacement arrives, so Mario stopped indefinitely.

V24 keeps V23's exact Mesen branch evaluator and V22 lifecycle containment but:

- accepts any individually proven, still-fresh safe branch without waiting for
  slow UNKNOWN workers,
- requires a result to simulate at least the full execution prefix before it can
  certify that prefix (unless the event is WIN),
- degrades after the prefix to the trajectory's declared tail action instead of
  held RELEASE. Jump plans therefore fall back to RIGHT+B, releasing A for future
  re-arm while preserving motion.

This is still Phase-A receding-horizon control, not the final beam-search planner.
"""

from __future__ import annotations

from pathlib import Path

from fami_pixel.games.smb1.forward_live import (
    execution_prefix_with_continuation,
    select_fresh_partial_safe_response,
)

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v23 as v23


PLANNER_NAME = "v24-forward-model-partial"
_BASE_AUTHORITY_MAIN = v23.authority_main


def _best_forward_plan_partial(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    """Choose a fresh proven prefix without a complete-cohort barrier."""

    responses: list[dict] = []
    for path in response_paths:
        response = v11._read_json(path)
        if response is None or "error" in response:
            continue
        responses.append(dict(response))

    selected = select_fresh_partial_safe_response(
        responses,
        current_frame=current_frame,
        freshness=freshness,
        last_applied_generation=last_applied_generation,
        prefix_frames=v23.EXECUTION_PREFIX_FRAMES,
    )

    if selected is None:
        fresh_events: list[str] = []
        for response in responses:
            try:
                generation = int(response.get("generation", -1))
                root_frame = int(response.get("root_frame", -1))
            except (TypeError, ValueError):
                continue
            age = int(current_frame) - root_frame
            if generation <= int(last_applied_generation) or age < 0 or age > int(freshness):
                continue
            event = response.get("trajectory_event")
            frames = response.get("trajectory_frames")
            fresh_events.append(f"{event}:{frames}f")

        v23._latest_forward_meta = {
            "forward_model_status": "waiting-partial",
            "forward_model_event": None,
            "forward_model_responses": len(responses),
            "forward_model_fresh_events": sorted(fresh_events),
        }
        return None

    result = dict(selected)
    generation = int(result["generation"])
    source_root_frame = int(result["root_frame"])
    source_age = int(current_frame) - source_root_frame

    # The Mesen proof belongs to the saved source root. Apply only the validated
    # short prefix now and audit the source age explicitly.
    result["trajectory_root_frame"] = source_root_frame
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = int(current_frame)
    result["age"] = 0
    result["cohort_size"] = len(responses)
    result["cohort_generation"] = generation
    result["guard_mode"] = (
        "forward-model-partial["
        f"{result.get('trajectory_event')},"
        f"src-age:{source_age}f,"
        f"sim:{result.get('trajectory_frames')}f]"
    )
    result["live_radar"] = dict(live_radar or {})

    v23._latest_forward_meta = {
        "forward_model_status": "selected-partial",
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
        "forward_model_responses": len(responses),
    }
    return result


def authority_main(args) -> int:
    v11._log(
        "Planner V24: latency-tolerant forward model enabled | "
        f"prefix={v23.EXECUTION_PREFIX_FRAMES}f partial-safe selection + tail continuation"
    )
    return _BASE_AUTHORITY_MAIN(args)


def _install_v24_overrides() -> None:
    # V23 uses module globals at runtime. Patch only the two failed integration
    # contracts; keep its workers, evaluator, Job Object, archive, telemetry, and
    # supervisor behavior otherwise unchanged.
    v23.PLANNER_NAME = PLANNER_NAME
    v23._best_forward_plan = _best_forward_plan_partial
    v23.execution_prefix_schedule = execution_prefix_with_continuation
    v23.authority_main = authority_main

    # V23 supervisor and worker spawners intentionally launch Path(__file__).
    # Point that global at this wrapper so every child receives the same fixes.
    v23.__file__ = __file__


def main() -> int:
    _install_v24_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
