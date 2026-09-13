#!/usr/bin/env python3
"""Drive the M0 controller sequence through Mesen debugger input overrides.

This verifies the exact DebugControllerState ABI and frame-aligned action path.
Game-state observation is intentionally deferred to the next slice, so this
probe proves command delivery and bounded stepping, not Mario movement yet.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    available_input_overrides,
    released_state,
    right_state,
    set_input_override,
)


READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run RIGHT x60, RIGHT+A x10, RELEASE through Mesen input overrides."
    )
    parser.add_argument("rom", type=Path, help="Path to a local NES ROM")
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--controller", type=int, default=0, help="Debugger input slot (default: 0)")
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def wait_for_stop(core: MesenCore, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if core.is_execution_stopped():
            return True
        time.sleep(0.001)
    return False


def step_action(
    core: MesenCore,
    controller: int,
    state,
    label: str,
    first_frame: int,
    count: int,
    timeout: float,
) -> int:
    set_input_override(core, controller, state)
    last_frame = first_frame - 1
    for frame in range(first_frame, first_frame + count):
        core.step_ppu_frame(1)
        if not wait_for_stop(core, timeout):
            print(f"Frame {frame:03d}: FAIL action={label} (no stopped state)", flush=True)
            raise SystemExit(4)
        print(f"Frame {frame:03d}: PASS action={label}", flush=True)
        last_frame = frame
    return last_frame


def worker(args: argparse.Namespace) -> None:
    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)

    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)
    if not core.load_rom(args.rom):
        print("LoadRom   : FAIL", flush=True)
        raise SystemExit(1)
    print("LoadRom   : PASS", flush=True)

    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)

    slots = available_input_overrides(core)
    print("InputSlots: " + " ".join(f"{i}={'yes' if value else 'no'}" for i, value in enumerate(slots)), flush=True)
    if not 0 <= args.controller < 8 or not slots[args.controller]:
        print(f"Controller: FAIL (slot {args.controller} unavailable)", flush=True)
        raise SystemExit(5)
    print(f"Controller: PASS (slot {args.controller})", flush=True)

    frame = 1
    frame = step_action(core, args.controller, right_state(), "RIGHT", frame, 60, args.step_timeout) + 1
    frame = step_action(core, args.controller, right_state(jump=True), "RIGHT+A", frame, 10, args.step_timeout) + 1
    # Zero state removes the debugger's forced buttons; because headless init uses
    # noInput=true, the underlying host input state is neutral.
    frame = step_action(core, args.controller, released_state(), "RELEASE", frame, 2, args.step_timeout) + 1

    print(f"ControlTrace: PASS ({frame - 1} frame-aligned commands)", flush=True)
    print("Sequence    : RIGHT x60 -> RIGHT+A x10 -> RELEASE x2", flush=True)
    print("Observation : pending native RAM/CPU/PPU verification", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--home", str(args.home),
        "--controller", str(args.controller),
        "--step-timeout", str(args.step_timeout),
        "--timeout", str(args.timeout),
        "--worker",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    deadline = time.monotonic() + args.timeout
    ready = False
    assert process.stdout is not None
    try:
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                print(line, end="")
                if READY_MARKER in line:
                    ready = True
                    break
                continue
            if process.poll() is not None:
                break
            time.sleep(0.01)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

    if ready:
        print("WorkerExit: FORCED (expected containment)")
        print("Supervisor: PASS")
        return 0

    code = process.returncode
    print(f"Supervisor: FAIL (worker exit code {code})")
    return code if code not in (None, 0) else 3


def main() -> int:
    args = parse_args()
    if args.worker:
        worker(args)
        return 0
    return supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
