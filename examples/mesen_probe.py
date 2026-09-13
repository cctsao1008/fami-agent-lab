"""Minimal M0 example: inspect a local MesenCore.dll without starting emulation."""

from __future__ import annotations

import argparse

from fami_pixel.adapters.mesen import MesenCore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dll", help="Path to MesenCore.dll")
    args = parser.parse_args()

    core = MesenCore(args.dll)
    print(f"Mesen version: {core.version()}")
    print(f"Build date   : {core.build_date()}")
    print(f"Smoke test   : {core.smoke_test()}")


if __name__ == "__main__":
    main()
