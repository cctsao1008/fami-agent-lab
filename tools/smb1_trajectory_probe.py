#!/usr/bin/env python3
"""Evaluate bounded SMB1 trajectories from one local Mesen scenario root.

This is a lab tool for issue #32, not yet the live V23 authority planner. It lets
us test real Mesen futures from a deterministic local checkpoint before wiring
the same evaluator into the asynchronous live search.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import (
    ActionCommand,
    Smb1Action,
    TrajectoryPlan,
    evaluate_mesen_trajectory,
    observation_from_state,
    read_smb1_state,
    trajectory_outcome_key,
)

_DONE = "TrajectoryProbe: DONE"
_GRACE_S = 0.75


PLANS = (
    TrajectoryPlan(
        "run",
        (ActionCommand(Smb1Action.RIGHT_B, 8),),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "coast",
        (ActionCommand(Smb1Action.NOOP, 6),),
        tail_action=Smb1Action.NOOP,
    ),
    TrajectoryPlan(
        "short_jump",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 7),
        ),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "long_jump",
        (
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 15),
        ),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "brake_jump",
        (
            ActionCommand(Smb1Action.LEFT_B, 4),
            ActionCommand(Smb1Action.NOOP, 2),
            ActionCommand(Smb1Action.RIGHT_B, 1),
            ActionCommand(Smb1Action.RIGHT_A_B, 15),
        ),
        tail_action=Smb1Action.RIGHT_B,
    ),
    TrajectoryPlan(
        "backtrack",
        (ActionCommand(Smb1Action.LEFT_B, 8),),
        tail_action=Smb1Action.NOOP,
    ),
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe Mesen trajectory outcomes from a saved SMB1 scenario")
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-trajectory-probe"))
    p.add_argument("--max-horizon", type=int, default=96)
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument("--target-reward", choices=("mushroom", "fire_flower", "star", "one_up"))
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def _restore(core: MesenCore, state_file: Path, manifest: dict) -> None:
    core.load_state_file(state_file)
    expected_frame = manifest.get("native_frame")
    expected_x = manifest.get("mario_x")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        state = read_smb1_state(core)
        frame_ok = expected_frame is None or core.frame_count() == int(expected_frame)
        x_ok = expected_x is None or state.player_absolute_x == int(expected_x)
        if frame_ok and x_ok:
            return
        time.sleep(0.005)
    state = read_smb1_state(core)
    raise RuntimeError(
        "scenario restore did not converge: "
        f"expected frame={expected_frame} x={expected_x}, "
        f"actual frame={core.frame_count()} x={state.player_absolute_x}"
    )


def _load_manifest(scenario_dir: Path) -> tuple[Path, dict, Path]:
    manifest_path = scenario_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            "scenario manifest not found: "
            f"{manifest_path}\n"
            "The scenario extractor must succeed before running the trajectory probe."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid scenario manifest: {manifest_path}: {exc}") from exc
    state_file = scenario_dir / str(manifest.get("state_file", "root.mss"))
    if not state_file.is_file():
        raise SystemExit(f"scenario state not found: {state_file}")
    return manifest_path, manifest, state_file


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    _manifest_path, manifest, state_file = _load_manifest(scenario_dir)

    target_reward = args.target_reward or manifest.get("selection_reward_type")
    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()

    print(f"Scenario   : {manifest.get('id')}", flush=True)
    print(f"Root       : generation={manifest.get('root_generation')} frame={manifest.get('native_frame')} X={manifest.get('mario_x')}", flush=True)
    print(f"Target     : {target_reward or 'navigation'}", flush=True)
    print(f"Horizon    : {args.max_horizon} frames", flush=True)

    results = []
    try:
        for plan in PLANS:
            _restore(core, state_file, manifest)
            start = observation_from_state(core.frame_count(), read_smb1_state(core))
            result = evaluate_mesen_trajectory(
                core,
                plan,
                max_horizon_frames=args.max_horizon,
                step_timeout_s=args.step_timeout,
                target_reward_type=target_reward,
                start_observation=start,
                stop_on_landing=target_reward is None,
            )
            results.append(result)
            approach = result.target_approach
            print(
                f"{plan.name:12s} event={result.event.value:19s} "
                f"frames={result.frames_simulated:3d} dx={result.progress:+4d} "
                f"maxdx={result.max_progress:+4d} landed={int(result.landed)} "
                f"reward={int(result.reward_collected)} "
                f"approach={'--' if approach is None else f'{approach:+d}'}",
                flush=True,
            )

        best = max(results, key=trajectory_outcome_key)
        print(
            f"SELECT     : {best.plan.name} -> {best.event.value} "
            f"key={trajectory_outcome_key(best)}",
            flush=True,
        )
        print(_DONE, flush=True)
        return 0
    finally:
        # Normal teardown is best-effort. The supervisor below owns the final
        # containment boundary if native Mesen teardown stalls.
        try:
            core.stop()
            core.release()
        except Exception:
            pass


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()


def supervise(args: argparse.Namespace) -> int:
    # Validate the local scenario before spawning the worker, so extraction
    # failures produce one concise message instead of a child traceback.
    _load_manifest(args.scenario_dir.expanduser().resolve())

    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    saw_done = False
    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            if _DONE in line:
                saw_done = True
                try:
                    return proc.wait(timeout=_GRACE_S)
                except subprocess.TimeoutExpired:
                    _terminate_tree(proc)
                    return 0
        return proc.wait()
    except KeyboardInterrupt:
        _terminate_tree(proc)
        return 130
    finally:
        if saw_done and proc.poll() is None:
            _terminate_tree(proc)


def main() -> int:
    args = parse_args()
    return worker(args) if args.worker else supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
