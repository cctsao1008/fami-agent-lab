#!/usr/bin/env python3
"""Concurrent receding-horizon SMB1 controller.

V11 changes the execution model from stop-plan-commit to continuous authority:
- authoritative Mesen steps continuously,
- authoritative observations/UI continue while planning runs,
- a separate shadow Mesen process evaluates the newest checkpoint,
- stale plans are discarded instead of stalling Mario.

This file intentionally starts with a small, auditable two-process protocol.
V10 remains the synchronous research oracle for candidate scoring semantics.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from fami_pixel.adapters.mesen import MesenCore, NES_B, NES_RIGHT, configure_standard_nes_controller
from fami_pixel.games.smb1 import (
    CandidateTerminal,
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)
from fami_pixel.telemetry import NesWebViewer

import mesen_smb_checkpoint_planner as base
import mesen_smb_checkpoint_planner_v7 as v7
import mesen_smb_checkpoint_planner_v8 as v8
import mesen_smb_checkpoint_planner_v10 as v10


CONTROL_QUANTUM = 4
PLAN_FRESHNESS_FRAMES = 8
UI_STRIDE = 2
BOOTSTRAP_BUTTONS = NES_RIGHT | NES_B
PLANNER_ENV = "FAMI_PIXEL_V11_SHADOW"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Concurrent receding-horizon SMB1 planner")
    p.add_argument("rom", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-v11-authority"))
    p.add_argument("--shadow-home", type=Path, default=Path("build/mesen-home-v11-shadow"))
    p.add_argument("--checkpoint-dir", type=Path, default=Path("build/checkpoints/v11-live"))
    p.add_argument("--step-timeout", type=float, default=5.0)
    p.add_argument("--max-frames", type=int, default=18000)
    p.add_argument("--web-ui", action="store_true")
    p.add_argument("--web-port", type=int, default=8765)
    p.add_argument("--control-quantum", type=int, default=CONTROL_QUANTUM)
    p.add_argument("--plan-freshness", type=int, default=PLAN_FRESHNESS_FRAMES)
    p.add_argument("--shadow", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--request", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--response", type=Path, help=argparse.SUPPRESS)
    return p.parse_args()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _buttons_for_candidate(name: str) -> int:
    candidate = next((c for c in v7.COARSE_CANDIDATES if c.name == name), None)
    if candidate is None:
        candidate = next((c for c in v7.PRECISION_CANDIDATES if c.name == name), None)
    if candidate is None:
        return BOOTSTRAP_BUTTONS
    return candidate.commands[0].nes_buttons


def _candidate_pool():
    # Keep the first live version bounded. V10's pit-aware rollout semantics are
    # reused; later V11 revisions can restore adaptive V8/V9 search inside shadow.
    names = {
        "cruise",
        "run",
        "tap_jump",
        "medium_jump",
        "long_jump",
        "run_tap_jump",
        "run_medium_jump",
        "run_long_jump",
    }
    return tuple(c for c in v7.COARSE_CANDIDATES if c.name in names)


def shadow_main(args: argparse.Namespace) -> int:
    assert args.request is not None and args.response is not None
    core = MesenCore(args.dll)
    core.initialize_headless(args.shadow_home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        return 2
    core.initialize_debugger()

    last_generation = -1
    candidates = _candidate_pool()
    while True:
        req = _read_json(args.request)
        if req is None:
            time.sleep(0.002)
            continue
        generation = int(req.get("generation", -1))
        if generation <= last_generation:
            time.sleep(0.002)
            continue
        last_generation = generation
        checkpoint = Path(req["checkpoint"])
        root_frame = int(req["frame"])
        root_x = int(req["x"])
        root_engine = int(req["engine"])
        try:
            base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
            start = observation_from_state(core.frame_count(), read_smb1_state(core))
            best = None
            best_score = None
            for candidate in candidates:
                base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
                outcome = v10.run_candidate_pit_aware(core, candidate, start, args.step_timeout)
                score = (
                    -1 if outcome.terminal == CandidateTerminal.DEATH else 1,
                    1 if outcome.reached_flagpole else 0,
                    outcome.progress,
                    outcome.max_x,
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best = candidate
            assert best is not None
            _atomic_json(
                args.response,
                {
                    "generation": generation,
                    "root_frame": root_frame,
                    "candidate": best.name,
                    "buttons": int(best.commands[0].nes_buttons),
                    "planned_at": time.time(),
                },
            )
        except Exception as exc:
            _atomic_json(
                args.response,
                {
                    "generation": generation,
                    "root_frame": root_frame,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def authority_main(args: argparse.Namespace) -> int:
    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    request_path = checkpoint_dir / "request.json"
    response_path = checkpoint_dir / "response.json"
    checkpoint_a = checkpoint_dir / "live-a.mss"
    checkpoint_b = checkpoint_dir / "live-b.mss"

    shadow_cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--shadow-home", str(args.shadow_home),
        "--step-timeout", str(args.step_timeout),
        "--shadow",
        "--request", str(request_path),
        "--response", str(response_path),
    ]
    shadow = subprocess.Popen(shadow_cmd)

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        shadow.terminate()
        return 2
    core.initialize_debugger()
    state = base.enter_world_1_1(core, args.step_timeout)
    current = observation_from_state(core.frame_count(), state)
    previous = current

    viewer = None
    if args.web_ui:
        viewer = NesWebViewer(port=args.web_port, playback_fps=30.0)
        viewer.start()
        print(f"Web UI    : {viewer.url}", flush=True)

    print(
        f"Planner V11: concurrent authority + shadow planning; "
        f"control quantum={args.control_quantum}f freshness={args.plan_freshness}f",
        flush=True,
    )

    applied_buttons = BOOTSTRAP_BUTTONS
    applied_label = "BOOTSTRAP RIGHT+B"
    generation = 0
    last_plan_generation = -1
    last_plan_root = -1

    try:
        for loop_index in range(args.max_frames):
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
                    mode="CONCURRENT RHC",
                    action=applied_label,
                    metadata={
                        "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                        "plan_age": (
                            current.native_frame_id - last_plan_root
                            if last_plan_root >= 0 else None
                        ),
                        "planner_state": "live",
                    },
                )

            if any(e.kind == GameEventType.DIED for e in events):
                print(
                    f"PlannerV11: FAIL death | frame={current.native_frame_id} "
                    f"X={current.mario_x_abs}",
                    flush=True,
                )
                return 6
            if any(e.kind == GameEventType.LEVEL_COMPLETED for e in events):
                print(
                    f"PlannerV11: PASS level complete | frame={current.native_frame_id} "
                    f"X={current.mario_x_abs}",
                    flush=True,
                )
                return 0

            if loop_index % max(1, args.control_quantum) == 0:
                # Consume latest plan without waiting for it.
                response = _read_json(response_path)
                if response is not None and "error" not in response:
                    plan_generation = int(response.get("generation", -1))
                    plan_root = int(response.get("root_frame", -1))
                    age = current.native_frame_id - plan_root
                    if (
                        plan_generation > last_plan_generation
                        and 0 <= age <= args.plan_freshness
                    ):
                        applied_buttons = int(response["buttons"])
                        applied_label = str(response["candidate"])
                        last_plan_generation = plan_generation
                        last_plan_root = plan_root

                # Publish newest authoritative snapshot for shadow planning.
                checkpoint = checkpoint_a if generation % 2 == 0 else checkpoint_b
                frame, x, engine = base.save_checkpoint(core, checkpoint)
                generation += 1
                _atomic_json(
                    request_path,
                    {
                        "generation": generation,
                        "checkpoint": str(checkpoint),
                        "frame": frame,
                        "x": x,
                        "engine": engine,
                    },
                )

            previous = current

        print(
            f"PlannerV11: FAIL frame limit | frame={current.native_frame_id} "
            f"X={current.mario_x_abs}",
            flush=True,
        )
        return 7
    finally:
        base.set_nes_controller_state(core, 0, 0x00)
        if viewer is not None:
            viewer.stop()
        if shadow.poll() is None:
            shadow.terminate()
            try:
                shadow.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                shadow.kill()


def main() -> int:
    args = parse_args()
    if args.shadow or os.environ.get(PLANNER_ENV) == "1":
        return shadow_main(args)
    return authority_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
