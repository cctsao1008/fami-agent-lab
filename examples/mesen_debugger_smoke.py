#!/usr/bin/env python3
"""Probe headless Mesen debugger bring-up inside a supervised worker process.

The current pinned Mesen CE build can deadlock during graceful teardown after the
headless debugger has been initialized. Keep that native failure domain inside a
child process so the caller retains control while the teardown contract is still
under audit.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore


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
        os._exit(1)
    print("LoadRom   : PASS", flush=True)
    print(f"IsRunning : {core.is_running()}", flush=True)

    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)
    print(f"DbgRunning: {core.is_debugger_running()}", flush=True)
    print(f"ExecStop  : {core.is_execution_stopped()}", flush=True)

    # Do not call Stop(), ReleaseDebugger(), or Release() here yet. Two real
    # Windows runs showed that the pinned native build can block indefinitely in
    # teardown after headless debugger initialization. The worker process is the
    # containment boundary while that upstream lifetime contract is audited.
    print("WorkerExit: HARD (teardown audit pending)", flush=True)
    os._exit(0)


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

    try:
        completed = subprocess.run(command, timeout=args.timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"Supervisor: FAIL (worker exceeded {args.timeout:g}s and was terminated)")
        return 2

    if completed.returncode != 0:
        print(f"Supervisor: FAIL (worker exit code {completed.returncode})")
        return completed.returncode

    print("Supervisor: PASS")
    return 0


def main() -> int:
    args = parse_args()
    if args.worker:
        worker(args)
        return 0
    return supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
