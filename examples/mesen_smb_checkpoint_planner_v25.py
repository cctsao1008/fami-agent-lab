#!/usr/bin/env python3
"""V25 live planner: sticky COLLECT objective with exact 4-frame reward prefixes.

V24 proved latency-tolerant partial forward-model control. The deterministic V5
reward beam then proved that the previously-missed Star is collectible once the
action vocabulary can sustain A across control quanta.

V25 is the first live integration of that result. It does *not* replay the V5
22-chunk fixture path. Instead, when a native power-up object exists, authority
creates a bounded sticky COLLECT objective and shadow Mesen workers evaluate the
same eight V5 chunks from the latest checkpoint. Only one exact 4-frame prefix is
applied, then authority re-observes and replans.

The reward frontier is deliberately shallow in this first live slice so it can
meet the existing asynchronous deadline. Exact Mesen proves each applied prefix
survives those four frames; native capability state proves collection. Normal
V24 event-horizon planning resumes as soon as the COLLECT objective ends.
"""

from __future__ import annotations

from pathlib import Path
import time

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    GameEventType,
    TrajectoryEvent,
    derive_game_events,
    evaluate_mesen_trajectory,
    observation_from_state,
    read_smb1_state,
    trajectory_outcome_key,
)
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.games.smb1.reward_beam import (
    REWARD_BEAM_CHUNKS_WITH_HOLD,
    buttons_for_chunk_frame,
    reward_collection_proven,
)
from fami_pixel.games.smb1.reward_live import (
    StickyCollectObjective,
    reward_chunk_schedule,
    select_fresh_reward_response,
)
from fami_pixel.games.smb1.reward_motion import reward_intercept_key_motion
from fami_pixel.games.smb1.reward_target import read_active_reward_target

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v17 as v17
import mesen_smb_checkpoint_planner_v23 as v23
import mesen_smb_checkpoint_planner_v24 as v24


PLANNER_NAME = "v25-live-reward-intercept"
_REWARD_PREFIX_FRAMES = 4
_LIVE_OBJECTIVE = StickyCollectObjective(ttl_frames=24)
_BASE_RADAR_READ = v17.read_smb1_radar
_BASE_FORWARD_LABEL = v23._forward_schedule_label
_BASE_V24_AUTHORITY = v24.authority_main

_REWARD_JUMP_NAMES = {
    "collect_rearm_right_jump4",
    "collect_rearm_left_jump4",
    "collect_hold_right_jump4",
    "collect_hold_left_jump4",
}


def _collect_target_from_radar(radar: dict) -> str | None:
    value = radar.get("collect_target_type") or radar.get("nearest_reward_type")
    if value in {"mushroom", "fire_flower", "star", "one_up"}:
        return str(value)
    return None


def _augmenting_read_smb1_radar(core, *, player_x: int, **kwargs):
    """Preserve normal radar semantics while attaching a sticky reward objective."""

    normal = _BASE_RADAR_READ(core, player_x=player_x, **kwargs)
    payload = normal.to_payload()
    try:
        tracked = read_active_reward_target(core, player_x=player_x)
    except Exception:
        tracked = None
    objective = _LIVE_OBJECTIVE.update(
        frame=core.frame_count(),
        radar=payload,
        tracked_reward=tracked,
    )
    payload["objective_mode"] = objective["mode"]
    payload["collect_target_type"] = objective["target_type"]
    payload["collect_target"] = objective["target"]
    payload["collect_target_sticky"] = bool(objective.get("sticky", False))
    if objective.get("collected_type") is not None:
        payload["reward_collected_type"] = objective["collected_type"]

    class _PayloadProxy:
        def to_payload(self_nonlocal):
            return dict(payload)

    return _PayloadProxy()


def _reward_chunks_for_worker(worker_index: int, worker_count: int):
    chunks = tuple(
        chunk
        for index, chunk in enumerate(REWARD_BEAM_CHUNKS_WITH_HOLD)
        if index % max(1, int(worker_count)) == int(worker_index)
    )
    if chunks:
        return chunks
    return (REWARD_BEAM_CHUNKS_WITH_HOLD[int(worker_index) % len(REWARD_BEAM_CHUNKS_WITH_HOLD)],)


def _evaluate_reward_chunk(core, chunk, *, target_type: str, request_radar: dict, step_timeout: float):
    """Run one exact reward prefix and return endpoint evidence/ranking."""

    previous = observation_from_state(core.frame_count(), read_smb1_state(core))
    current = previous
    radar = read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
    baseline_status = int(request_radar.get("player_status", radar.get("player_status", 0)))
    baseline_timer = int(request_radar.get("star_invincible_timer", radar.get("star_invincible_timer", 0)))
    died = False
    won = False
    collected = False
    frames = 0

    for offset in range(chunk.frame_count):
        set_nes_controller_state(core, 0, buttons_for_chunk_frame(chunk, offset))
        core.step_frame_sync(1, max(1, int(float(step_timeout) * 1000.0)))
        frames += 1
        current = observation_from_state(core.frame_count(), read_smb1_state(core))
        radar = read_smb1_radar(core, player_x=current.mario_x_abs).to_payload()
        events = derive_game_events(previous, current)
        died = any(event.kind == GameEventType.DIED for event in events)
        won = any(event.kind == GameEventType.LEVEL_COMPLETED for event in events)
        collected = reward_collection_proven(
            target_type,
            baseline_player_status=baseline_status,
            baseline_star_timer=baseline_timer,
            radar=radar,
        )
        if died or won or collected:
            break
        previous = current

    try:
        set_nes_controller_state(core, 0, 0x00)
    except Exception:
        pass

    try:
        tracked = read_active_reward_target(core, player_x=current.mario_x_abs)
    except Exception:
        tracked = None
    if tracked is not None and str(tracked.get("type")) != target_type:
        tracked = None

    enemy = radar.get("nearest_enemy_dx")
    enemy_dx = None if enemy is None else int(enemy)
    key = reward_intercept_key_motion(
        reward=tracked,
        nearest_enemy_dx=enemy_dx,
        mario_y=int(current.mario_y),
        player_x_speed=int(current.player_x_speed),
        player_y_speed=int(current.player_y_speed),
    )
    return {
        "died": died,
        "won": won,
        "collected": collected,
        "frames": frames,
        "observation": current,
        "radar": radar,
        "target": tracked,
        "reward_key": key,
        "nearest_enemy_dx": enemy_dx,
    }


def _baseline_payload(core, args, req: dict, *, generation: int, root_frame: int, root_x: int, root_engine: int):
    plans = v23._worker_plans(args.worker_index, args.worker_count)
    started = time.perf_counter()
    best_result = None
    for plan in plans:
        base.restore_checkpoint(core, Path(req["checkpoint"]), root_frame, root_x, root_engine)
        start = observation_from_state(core.frame_count(), read_smb1_state(core))
        result = evaluate_mesen_trajectory(
            core,
            plan,
            max_horizon_frames=v23.LIVE_TRAJECTORY_HORIZON,
            step_timeout_s=args.step_timeout,
            target_reward_type=None,
            start_observation=start,
            stop_on_landing=True,
        )
        if best_result is None or trajectory_outcome_key(result) > trajectory_outcome_key(best_result):
            best_result = result

    assert best_result is not None
    event = best_result.event
    return {
        "generation": generation,
        "worker": args.worker_index,
        "root_frame": root_frame,
        "planner_mode": "progress",
        "candidate": best_result.plan.name,
        "schedule": v23.execution_prefix_schedule(best_result.plan, v23.EXECUTION_PREFIX_FRAMES),
        "score": list(trajectory_outcome_key(best_result)),
        "progress": int(best_result.progress),
        "terminal": "death" if event == TrajectoryEvent.DEATH else "none",
        "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "trajectory_event": event.value,
        "trajectory_safe_resolved": bool(v23.result_is_safe_resolved(best_result)),
        "trajectory_frames": int(best_result.frames_simulated),
        "trajectory_end_x": int(best_result.end_x),
        "trajectory_max_x": int(best_result.max_x),
        "trajectory_end_y": int(best_result.end_y),
        "trajectory_landed": bool(best_result.landed),
        "trajectory_died": bool(best_result.died),
        "trajectory_reward_collected": bool(best_result.reward_collected),
        "trajectory_target_reward_type": best_result.target_reward_type,
        "trajectory_target_approach": best_result.target_approach,
        "risk_probability": 0.0,
        "no_progress_probability": 0.0,
    }


def shadow_worker_main(args) -> int:
    """Switch each private Mesen worker between PROGRESS and COLLECT prefixes."""

    assert args.request is not None and args.response is not None
    reward_chunks = _reward_chunks_for_worker(args.worker_index, args.worker_count)

    worker_home = Path(f"{args.shadow_home}-v25-{args.worker_index}")
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
        request_radar = dict(req.get("radar") or {})
        target_type = _collect_target_from_radar(request_radar)

        try:
            if target_type is None:
                payload = _baseline_payload(
                    core,
                    args,
                    req,
                    generation=generation,
                    root_frame=root_frame,
                    root_x=root_x,
                    root_engine=root_engine,
                )
                v11._atomic_json(args.response, payload)
                continue

            started = time.perf_counter()
            best = None
            best_key = None
            for chunk in reward_chunks:
                base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
                outcome = _evaluate_reward_chunk(
                    core,
                    chunk,
                    target_type=target_type,
                    request_radar=request_radar,
                    step_timeout=args.step_timeout,
                )
                safe = not bool(outcome["died"])
                key = (
                    1 if bool(outcome["collected"]) else 0,
                    tuple(outcome["reward_key"]),
                )
                if safe and (best_key is None or key > best_key):
                    best_key = key
                    best = (chunk, outcome)

            if best is None:
                payload = {
                    "generation": generation,
                    "worker": args.worker_index,
                    "root_frame": root_frame,
                    "planner_mode": "collect",
                    "target_reward_type": target_type,
                    "reward_prefix_safe": False,
                    "terminal": "death",
                    "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
                }
            else:
                chunk, outcome = best
                current = outcome["observation"]
                target = outcome["target"]
                payload = {
                    "generation": generation,
                    "worker": args.worker_index,
                    "root_frame": root_frame,
                    "planner_mode": "collect",
                    "target_reward_type": target_type,
                    "candidate": f"collect_{chunk.name}",
                    "schedule": reward_chunk_schedule(chunk),
                    "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "terminal": "none",
                    "reward_prefix_safe": True,
                    "reward_collected": bool(outcome["collected"]),
                    "reward_key": list(outcome["reward_key"]),
                    "reward_target_dx": None if target is None else int(target.get("dx", 0)),
                    "reward_target_state": None if target is None else int(target.get("state", 0)),
                    "reward_target_y": None if target is None else int(target.get("y", 0)),
                    "reward_prefix_frames": int(outcome["frames"]),
                    "trajectory_event": "reward_collected" if outcome["collected"] else "prefix_alive",
                    "trajectory_frames": int(outcome["frames"]),
                    "trajectory_end_x": int(current.mario_x_abs),
                    "trajectory_end_y": int(current.mario_y),
                    "trajectory_reward_collected": bool(outcome["collected"]),
                    "trajectory_target_reward_type": target_type,
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


def _best_v25_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    target_type = _collect_target_from_radar(live_radar)
    if target_type is None:
        return v24._best_forward_plan_partial(
            response_paths,
            current_frame,
            freshness,
            last_applied_generation,
            live_radar,
        )

    responses: list[dict] = []
    for path in response_paths:
        response = v11._read_json(path)
        if response is not None:
            responses.append(dict(response))

    selected = select_fresh_reward_response(
        responses,
        current_frame=current_frame,
        freshness=freshness,
        last_applied_generation=last_applied_generation,
        target_type=target_type,
    )
    if selected is None:
        v23._latest_forward_meta = {
            "forward_model_status": "waiting-collect-prefix",
            "objective_mode": "COLLECT",
            "collect_target_type": target_type,
            "forward_model_responses": len(responses),
        }
        return None

    result = dict(selected)
    source_root_frame = int(result["root_frame"])
    source_age = int(current_frame) - source_root_frame
    result["trajectory_root_frame"] = source_root_frame
    result["trajectory_source_age_frames"] = source_age
    result["root_frame"] = int(current_frame)
    result["age"] = 0
    result["guard_mode"] = (
        f"collect-prefix[{target_type},{result.get('candidate')},"
        f"src-age:{source_age}f]"
    )
    result["live_radar"] = dict(live_radar or {})

    v23._latest_forward_meta = {
        "forward_model_status": "selected-collect-prefix",
        "objective_mode": "COLLECT",
        "collect_target_type": target_type,
        "collect_target_sticky": bool(live_radar.get("collect_target_sticky", False)),
        "forward_model_generation": result.get("generation"),
        "forward_model_plan": result.get("candidate"),
        "forward_model_event": result.get("trajectory_event"),
        "forward_model_source_frame": source_root_frame,
        "forward_model_source_age_frames": source_age,
        "forward_model_simulated_frames": result.get("trajectory_frames"),
        "forward_model_end_x": result.get("trajectory_end_x"),
        "forward_model_end_y": result.get("trajectory_end_y"),
        "forward_model_reward_collected": result.get("reward_collected"),
        "forward_model_target_reward_type": target_type,
        "reward_target_dx": result.get("reward_target_dx"),
        "reward_target_state": result.get("reward_target_state"),
        "reward_target_y": result.get("reward_target_y"),
        "forward_model_responses": len(responses),
    }
    return result


def _v25_schedule_label(candidate_name: str) -> str:
    if candidate_name.startswith("collect_"):
        chunk = candidate_name[len("collect_") :]
        return f"COLLECT {chunk} | execute {_REWARD_PREFIX_FRAMES}f then replan"
    return _BASE_FORWARD_LABEL(candidate_name)


def authority_main(args) -> int:
    _LIVE_OBJECTIVE.clear()
    v17.read_smb1_radar = _augmenting_read_smb1_radar
    v11._log(
        "Planner V25: sticky COLLECT objective enabled | "
        f"reward-prefix={_REWARD_PREFIX_FRAMES}f vocab={len(REWARD_BEAM_CHUNKS_WITH_HOLD)} "
        "exact Mesen prefix safety + native collection proof"
    )
    return _BASE_V24_AUTHORITY(args)


def _install_v25_overrides() -> None:
    v23.PLANNER_NAME = PLANNER_NAME
    v23.shadow_worker_main = shadow_worker_main
    v23._best_forward_plan = _best_v25_plan
    v23._forward_schedule_label = _v25_schedule_label
    v23.authority_main = authority_main
    v23.__file__ = __file__
    v14._schedule_label = _v25_schedule_label
    v15._JUMP_NAMES.update(_REWARD_JUMP_NAMES)


def main() -> int:
    # Install V24 first because it changes the authority wrapper and continuation
    # semantics. Then replace only the reward-aware worker/selector layer.
    v24._install_v24_overrides()
    _install_v25_overrides()
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())
