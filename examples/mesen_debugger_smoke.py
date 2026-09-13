#!/usr/bin/env python3
"""Probe headless Mesen debugger bring-up inside a supervised worker process.

The current pinned Mesen CE build can deadlock or fail-fast during process
teardown after the headless debugger has been initialized. Keep that native
failure domain inside a child process and let the supervisor terminate the child
once the probe result has been emitted.
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
        description="Load a local NES ROM headlessly and initialize Mesen's debugger."
    )
    parser.add_argument("rom", type=Path, help="Path to a local NES ROM")
    parser.add_argument(
        "--dll",
        type=Path,
        default=Path("build/mesen/MesenCore.dll"),
        help="Path to MesenCore.dll (default: build/mesen/MesenCore.dll)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path("build/mesen-home"),
        help="Mesen home directory (default: build/mesen-home)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Worker timeout in seconds (default: 10)",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def worker(args: argparse.Namespace) -> None:
    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"Version   : {core.version()}", flush=True)
    print(f"Build date: {core.build_date()}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)
    print(f"Home      : {args.home.expanduser().resolve()}", flush=True)

    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)

    if not core.load_rom(args.rom):
        print("LoadRom   : FAIL", flush=True)
        raise SystemExit(1)
    print("LoadRom   : PASS", flush=True)
    print(f"IsRunning : {core.is_running()}", flush=True)

    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)
    print(f"DbgRunning: {core.is_debugger_running()}", flush=True)
    print(f"ExecStop  : {core.is_execution_stopped()}", flush=True)

    # Do not run Python/CRT/DLL teardown in this worker. A real Windows run
    # reached this point and then exited with 0xC0000409 during process teardown.
    # Signal the supervisor and remain alive; on Windows Popen.terminate() maps to
    # TerminateProcess, which is intentionally the containment boundary here.
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

    returncode = process.returncode
    if returncode is None:
        print(f"Supervisor: FAIL (worker exceeded {args.timeout:g}s)")
        return 2

    print(f"Supervisor: FAIL (worker exited before ready marker, code {returncode})")
    return returncode if returncode != 0 else 3


def main() -> int:
    args = parse_args()
    if args.worker:
        worker(args)
        return 0
    return supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
