#!/usr/bin/env python3
"""Exercise the verified headless Mesen debugger lifecycle without stepping yet."""

from __future__ import annotations

import argparse
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    with MesenCore(args.dll) as core:
        print(f"DLL       : {core.path}")
        print(f"Version   : {core.version()}")
        print(f"Build date: {core.build_date()}")
        print(f"ROM       : {args.rom.expanduser().resolve()}")
        print(f"Home      : {args.home.expanduser().resolve()}")

        core.initialize_headless(args.home)
        print("Init      : PASS")

        if not core.load_rom(args.rom):
            print("LoadRom   : FAIL")
            return 1
        print("LoadRom   : PASS")
        print(f"IsRunning : {core.is_running()}")

        core.initialize_debugger()
        print("Debugger  : PASS")
        print(f"DbgRunning: {core.is_debugger_running()}")
        print(f"ExecStop  : {core.is_execution_stopped()}")

        # Keep the debugger attached during Stop(). Upstream Emulator::Stop()
        # owns debugger reset/teardown. Releasing the debugger first while the
        # emulation thread is live can leave Stop() waiting on that thread.
        core.stop()
        print("Stop      : PASS")
        print(f"DbgRunning: {core.is_debugger_running()}")

    print("Release   : PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
