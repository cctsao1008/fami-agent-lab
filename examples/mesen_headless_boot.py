#!/usr/bin/env python3
"""Headless Mesen CE ROM boot smoke test for M0.

This deliberately stops before debugger stepping, controller injection, or
structured state capture. Its purpose is to validate the native lifecycle:

    InitDll -> InitializeEmu(headless) -> LoadRom -> IsRunning -> Release
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore, MesenLoadError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Boot a local NES ROM in headless Mesen CE.")
    parser.add_argument("rom", type=Path, help="Path to a local .nes ROM")
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
        help="Isolated Mesen home folder (default: build/mesen-home)",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=250,
        help="Milliseconds to allow the emulator thread to start (default: 250)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        with MesenCore(args.dll) as core:
            print(f"DLL       : {core.path}")
            print(f"Version   : {core.version()}")
            print(f"Build date: {core.build_date()}")
            print(f"ROM       : {args.rom.expanduser().resolve()}")
            print(f"Home      : {args.home.expanduser().resolve()}")

            core.initialize_headless(args.home)
            print("Init      : PASS")

            if not core.load_rom(args.rom):
                print("LoadRom   : FAIL", file=sys.stderr)
                return 1
            print("LoadRom   : PASS")

            if args.settle_ms > 0:
                time.sleep(args.settle_ms / 1000.0)

            running = core.is_running()
            paused = core.is_paused()
            print(f"IsRunning : {running}")
            print(f"IsPaused  : {paused}")

            if not running:
                print("ERROR: ROM loaded but emulator did not report running.", file=sys.stderr)
                return 1

            core.stop()
            print("Stop      : PASS")

        print("Release   : PASS")
        return 0

    except MesenLoadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
