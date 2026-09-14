#!/usr/bin/env python3
"""Concurrent receding-horizon SMB1 controller.

V11 changes the execution model from stop-plan-commit to continuous authority:
- authoritative Mesen steps continuously,
- authoritative observations/UI continue while planning runs,
- multiple shadow Mesen processes evaluate the newest checkpoint in parallel,
- stale plans are discarded instead of stalling Mario.

The live planner deliberately uses short 8-12 frame probes. Longer V10-style
30-frame rollouts remain useful as an offline/synchronous oracle, but cannot meet
a 4-frame control deadline when native stepping is close to real time.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fami_pixel.adapters.mesen import (
    MesenCore,
    NES_A,
    NES_B,
    NES_RIGHT,
    configure_standard_nes_controller,
)
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
import mesen_smb_checkpoint_planner_v10 as v10


CONTROL_QUANTUM = 4
PLAN_FRESHNESS_FRAMES = 16
UI_STRIDE = 2
DEFAULT_SHADOW_WORKERS = 4
BOOTSTRAP_BUTTONS = NES_RIGHT | NES_B
BOOTSTRAP_SCHEDULE = (
    {"buttons": NES_RIGHT | NES_A | NES_B, "frames": 8},
    {"buttons": NES_RIGHT | NES_B, "frames": 8},
)
LIVE_CANDIDATE_NAMES = (
    "right_b_8",
    "right_a_b_8",
    "right_a_b_12",
    "right_8",
)
_TERMINAL_PREFIX = "PlannerV11:"
_SUPERVISOR_GRACE_S = 0.75
_IPC_REPLACE_ATTEMPTS = 4


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _log(message: str) -> None:
    print(f"[{_timestamp()}] {message}", flush=True)


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
    p.add_argument("--shadow-workers", type=int, default=DEFAULT_SHADOW_WORKERS)
    p.add_argument("--authority-worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--shadow-worker", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--worker-index", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--worker-count", type=int, default=1, help=argparse.SUPPRESS)
    p.add_argument("--request", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--response", type=Path, help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.control_quantum <= 0:
        p.error("--control-quantum must be > 0")
    if args.plan_freshness < 0:
        p.error("--plan-freshness must be >= 0")
    if args.shadow_workers <= 0:
        p.error("--shadow-workers must be > 0")
    return args


def _atomic_json(path: Path, payload: dict) -> bool:
    """Publish latest-value IPC without letting Windows sharing races stop authority.

    On Windows a reader can briefly hold the destination JSON open, causing
    ``os.replace`` to fail with WinError 5/32.  V11 is a latest-value protocol:
    a missed snapshot/result is preferable to blocking or killing the live plant.
    Use a unique temp file, retry only for a few milliseconds, then drop this
    publication and let the next generation supersede it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(",", ":"))

    for attempt in range(_IPC_REPLACE_ATTEMPTS):
        tmp = path.with_name(
            f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            tmp.write_text(encoded, encoding="utf-8")
            os.replace(tmp, path)
            return True
        except PermissionError:
            try:
                tmp.unlink()
            except OSError:
                pass
            if attempt + 1 < _IPC_REPLACE_ATTEMPTS:
                time.sleep(0.001 * (2**attempt))
                continue
            return False
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    return False


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _candidate_pool():
    by_name = {candidate.name: candidate for candidate in v7.PRECISION_CANDIDATES}
    return tuple(by_name[name] for name in LIVE_CANDIDATE_NAMES)


def _candidate_shard(worker_index: int, worker_count: int):
    candidates = _candidate_pool()
    return tuple(
        candidate
        for index, candidate in enumerate(candidates)
        if index % worker_count == worker_index
    )


def _score_outcome(outcome) -> tuple[int, int, int, int]:
    return (
        -1 if outcome.terminal == CandidateTerminal.DEATH else 1,
        1 if outcome.reached_flagpole else 0,
        outcome.progress,
        outcome.max_x,
    )


def _schedule_payload(candidate) -> list[dict[str, int]]:
    return [
        {"buttons": int(command.nes_buttons), "frames": int(command.frame_count)}
        for command in candidate.commands
    ]


def _schedule_label(candidate_name: str) -> str:
    return v7.action_label(candidate_name)


def shadow_worker_main(args: argparse.Namespace) -> int:
    assert args.request is not None and args.response is not None
    candidates = _candidate_shard(args.worker_index, args.worker_count)
    if not candidates:
        return 3

    worker_home = Path(f"{args.shadow_home}-{args.worker_index}")
    core = MesenCore(args.dll)
    core.initialize_headless(worker_home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        return 2
    core.initialize_debugger()

    last_generation = -1
    while True:
        req = _read_json(args.request)
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
        started = time.perf_counter()

        try:
            for candidate in candidates:
                base.restore_checkpoint(core, checkpoint, root_frame, root_x, root_engine)
                start = observation_from_state(core.frame_count(), read_smb1_state(core))
                outcome = v10.run_candidate_pit_aware(core, candidate, start, args.step_timeout)
                score = _score_outcome(outcome)
                if best_score is None or score > best_score:
                    best_score = score
                    best = candidate
                    best_outcome = outcome

            if best is not None and best_outcome is not None:
                _atomic_json(
                    args.response,
                    {
                        "generation": generation,
                        "worker": args.worker_index,
                        "root_frame": root_frame,
                        "candidate": best.name,
                        "schedule": _schedule_payload(best),
                        "score": list(best_score),
                        "progress": best_outcome.progress,
                        "terminal": best_outcome.terminal.value,
                        "compute_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    },
                )
        except Exception as exc:
            _atomic_json(
                args.response,
                {
                    "generation": generation,
                    "worker": args.worker_index,
                    "root_frame": root_frame,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def _spawn_shadow_workers(args: argparse.Namespace, request_path: Path, response_paths: list[Path]):
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
        workers.append(
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
    return workers


def _best_fresh_plan(response_paths: list[Path], current_frame: int, freshness: int, last_applied_generation: int):
    plans: list[dict] = []
    for path in response_paths:
        response = _read_json(path)
        if response is None or "error" in response:
            continue
        generation = int(response.get("generation", -1))
        root_frame = int(response.get("root_frame", -1))
        age = current_frame - root_frame
        if generation <= last_applied_generation or age < 0 or age > freshness:
            continue
        response["age"] = age
        plans.append(response)
    if not plans:
        return None
    return max(plans, key=lambda p: (int(p["root_frame"]), tuple(p.get("score", []))))


def _schedule_buttons(schedule: list[dict] | tuple[dict, ...], elapsed_frames: int, *, repeat: bool = False) -> int:
    if not schedule:
        return BOOTSTRAP_BUTTONS
    total = sum(int(segment["frames"]) for segment in schedule)
    remaining = max(0, int(elapsed_frames))
    if repeat and total > 0:
        remaining %= total
    for segment in schedule:
        frames = int(segment["frames"])
        if remaining < frames:
            return int(segment["buttons"])
        remaining -= frames
    return int(schedule[-1]["buttons"])


def _terminal_code(line: str) -> int | None:
    if not line.strip().startswith(_TERMINAL_PREFIX):
        return None
    if "PASS" in line:
        return 0
    if "FAIL death" in line:
        return 6
    if "FAIL frame limit" in line:
        return 7
    if "FAIL" in line:
        return 1
    return None


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        proc.kill()


def authority_main(args: argparse.Namespace) -> int:
    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    request_path = checkpoint_dir / "request.json"
    response_paths = [checkpoint_dir / f"response-{i}.json" for i in range(args.shadow_workers)]
    for stale in [request_path, *response_paths]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    workers = _spawn_shadow_workers(args, request_path, response_paths)

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
        _log(f"Web UI    : {viewer.url}")

    _log(
        f"Planner V11: continuous authority + {args.shadow_workers} parallel shadow workers; "
        f"control={args.control_quantum}f freshness={args.plan_freshness}f live-horizon=8..12f"
    )

    applied_schedule: list[dict] | tuple[dict, ...] = BOOTSTRAP_SCHEDULE
    applied_label = "BOOTSTRAP PULSE-JUMP"
    applied_plan_root = current.native_frame_id
    using_bootstrap = True
    generation = 0
    last_applied_generation = -1
    last_plan_root = -1
    last_plan_age = None
    last_plan_compute_ms = None

    try:
        for loop_index in range(args.max_frames):
            schedule_age = max(0, current.native_frame_id - applied_plan_root)
            applied_buttons = _schedule_buttons(
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
                    mode="CONCURRENT RHC",
                    action=applied_label,
                    metadata={
                        "plan_root_frame": last_plan_root if last_plan_root >= 0 else None,
                        "plan_age": last_plan_age,
                        "planner_state": f"parallel/{args.shadow_workers}",
                        "plan_compute_ms": last_plan_compute_ms,
                    },
                )

            if any(e.kind == GameEventType.DIED for e in events):
                _log(f"PlannerV11: FAIL death | frame={current.native_frame_id} X={current.mario_x_abs}")
                return 6
            if any(e.kind == GameEventType.LEVEL_COMPLETED for e in events):
                _log(f"PlannerV11: PASS level complete | frame={current.native_frame_id} X={current.mario_x_abs}")
                return 0

            if loop_index % args.control_quantum == 0:
                plan = _best_fresh_plan(
                    response_paths,
                    current.native_frame_id,
                    args.plan_freshness,
                    last_applied_generation,
                )
                if plan is not None:
                    applied_schedule = list(plan.get("schedule") or [])
                    applied_label = _schedule_label(str(plan["candidate"]))
                    applied_plan_root = int(plan["root_frame"])
                    using_bootstrap = False
                    last_applied_generation = int(plan["generation"])
                    last_plan_root = applied_plan_root
                    last_plan_age = int(plan["age"])
                    last_plan_compute_ms = float(plan.get("compute_ms", 0.0))
                    _log(
                        f"control update: {applied_label} root={last_plan_root} "
                        f"age={last_plan_age}f worker={plan.get('worker')} "
                        f"compute={last_plan_compute_ms:.1f}ms"
                    )

                generation += 1
                checkpoint = checkpoint_dir / f"live-{generation:06d}.mss"
                frame, x, engine = base.save_checkpoint(core, checkpoint)
                published = _atomic_json(
                    request_path,
                    {
                        "generation": generation,
                        "checkpoint": str(checkpoint),
                        "frame": frame,
                        "x": x,
                        "engine": engine,
                    },
                )
                if not published:
                    _log(
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

        _log(f"PlannerV11: FAIL frame limit | frame={current.native_frame_id} X={current.mario_x_abs}")
        return 7
    finally:
        # Do not let teardown correctness hide the terminal result. The outer
        # supervisor also owns a process-tree kill as the final Windows guard.
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
        code = _terminal_code(line[line.find("PlannerV11:"):] if "PlannerV11:" in line else line)
        if code is None:
            continue
        try:
            proc.wait(timeout=_SUPERVISOR_GRACE_S)
        except subprocess.TimeoutExpired:
            _log("V11 supervisor: terminal result observed; terminating authority + shadow process tree")
            _terminate_process_tree(proc)
        return code

    return proc.wait()


def main() -> int:
    args = parse_args()
    if args.shadow_worker:
        return shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())