#!/usr/bin/env python3
"""Probe one NES PPU-frame step through Mesen's debugger ABI.

The probe runs inside a supervised worker because the current pinned Mesen CE
build has an unresolved teardown failure after headless debugger initialization.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore


CPU_TYPE_NES = 8
STEP_TYPE_PPU_FRAME = 6
READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a NES ROM headlessly and request one debugger PPU-frame step."
    )
    parser.add_argument("rom", type=Path, help="Path to a local NES ROM")
    parser.add_argument(
        "--dll",
        type=Path,
        default=Path("build/mesen/MesenCore.dll"),
        help="Path to MesenCore.dll",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path("build/mesen-home"),
        help="Mesen home directory",
    )
    parser.add_argument(
        "--step-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for IsExecutionStopped() after Step()",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Supervisor worker timeout in seconds",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def bind_step(core: MesenCore):
    """Bind the exact pinned-source Step ABI for this experiment only."""
    step = core._dll.Step
    step.argtypes = [ctypes.c_uint8, ctypes.c_uint32, ctypes.c_int]
    step.restype = None
    return step


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
    print(f"BeforeStop: {core.is_execution_stopped()}", flush=True)

    step = bind_step(core)
    print("StepCall  : Nes / count=1 / PpuFrame", flush=True)
    step(CPU_TYPE_NES, 1, STEP_TYPE_PPU_FRAME)
    print("StepReturn: PASS", flush=True)

    deadline = time.monotonic() + args.step_timeout
    stopped = False
    while time.monotonic() < deadline:
        if core.is_execution_stopped():
            stopped = True
            break
        time.sleep(0.001)

    print(f"AfterStop : {stopped}", flush=True)
    if not stopped:
        print("FrameStep : FAIL (no stop observed before timeout)", flush=True)
        raise SystemExit(4)

    print("FrameStep : PASS (one PpuFrame request reached stopped state)", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
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
