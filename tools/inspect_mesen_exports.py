#!/usr/bin/env python3
"""Inspect the MesenCore.dll ABI needed by fami-pixel M0."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore, MesenLoadError
from fami_pixel.adapters.mesen.loader import M0_EXPORTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load MesenCore.dll and report M0-relevant exports."
    )
    parser.add_argument("dll", type=Path, help="Path to MesenCore.dll")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        core = MesenCore(args.dll)
    except MesenLoadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"DLL       : {core.path}")
    print(f"TestDll   : {'PASS' if core.smoke_test() else 'FAIL'}")
    print(f"Version   : {core.version()}")
    print(f"Build date: {core.build_date()}")
    print()
    print("M0 exports:")

    exports = core.available_exports(M0_EXPORTS)
    width = max(map(len, exports))
    for name, present in exports.items():
        print(f"  {name:<{width}} : {'yes' if present else 'NO'}")

    missing = [name for name, present in exports.items() if not present]
    if missing:
        print()
        print("Missing:")
        for name in missing:
            print(f"  - {name}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
