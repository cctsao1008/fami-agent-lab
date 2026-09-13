#!/usr/bin/env python3
"""Validate repeated NES PPU-frame stepping through the verified Mesen adapter.

The worker is supervised because graceful teardown after headless debugger
initialization is still unresolved for the pinned Mesen CE build.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore


READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Request repeated single-PPU-frame steps from Mesen."
    )
    parser.add_argument("rom", type=Path, help="Path to a local NES ROM")
    parser.add_argument("--frames", type=int, default=16, help="Number of frames to step")
    parser.add_argument(
        "--dll", type=Path, default=Path("build/mesen/MesenCore.dll")
    )
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--step-timeout",
        type=float,
        default=2.0,
        help="Per-frame timeout waiting for stopped state",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Supervisor timeout in seconds"
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def wait_for_stop(core: MesenCore, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if core.is_execution_stopped():
            return True
        time.sleep(0.001)
    return False


def worker(args: argparse.Namespace) -> None:
    if args.frames < 1:
        raise SystemExit("--frames must be >= 1")

    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)
    print(f"Frames    : {args.frames}", flush=True)

    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)

    if not core.load_rom(args.rom):
        print("LoadRom   : FAIL", flush=True)
        raise SystemExit(1)
    print("LoadRom   : PASS", flush=True)

    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)
    print(f"InitialStop: {core.is_execution_stopped()}", flush=True)

    durations_ms: list[float] = []
    for frame in range(1, args.frames + 1):
        start = time.perf_counter()
        core.step_ppu_frame(1)
        if not wait_for_stop(core, args.step_timeout):
            print(f"Frame {frame:03d}: FAIL (no stopped state)", flush=True)
            raise SystemExit(4)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        durations_ms.append(elapsed_ms)
        print(f"Frame {frame:03d}: PASS  {elapsed_ms:8.3f} ms", flush=True)

    avg_ms = sum(durations_ms) / len(durations_ms)
    print(f"RepeatedStep: PASS ({args.frames}/{args.frames})", flush=True)
    print(f"StepLatency : avg={avg_ms:.3f} ms min={min(durations_ms):.3f} ms max={max(durations_ms):.3f} ms", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--frames",
        str(args.frames),
        "--dll",
        str(args.dll),
        "--home",
        str(args.home),
        "--step-timeout",
        str(args.step_timeout),
        "--timeout",
        str(args.timeout),
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
