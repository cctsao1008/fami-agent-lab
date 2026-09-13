#!/usr/bin/env python3
"""Validate safe access to Mesen's canonical raw NES PPU framebuffer."""

from __future__ import annotations

import argparse
import array
import ctypes
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    MesenLoadError,
    NES_FRAME_HEIGHT,
    NES_FRAME_PIXEL_COUNT,
    NES_FRAME_WIDTH,
    configure_standard_nes_controller,
    copy_nes_raw_frame,
    set_nes_controller_state,
)

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify canonical NES PPU framebuffer copy through MesenCore.dll."
    )
    parser.add_argument("rom", type=Path, help="Path to a local NES ROM")
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--warmup-frames", type=int, default=120)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def frame_crc32(pixels: tuple[int, ...]) -> int:
    values = array.array("H", pixels)
    return zlib.crc32(values.tobytes()) & 0xFFFFFFFF


def worker(args: argparse.Namespace) -> None:
    if args.warmup_frames < 1:
        raise SystemExit("--warmup-frames must be >= 1")

    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)

    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)
    config = configure_standard_nes_controller(core, port=1)
    print(
        f"NesConfig : PASS (sizeof={ctypes.sizeof(config)} Port1.Type={config.Port1.Type})",
        flush=True,
    )
    if not core.load_rom(args.rom):
        print("LoadRom   : FAIL", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print("LoadRom   : PASS", flush=True)

    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)
    set_nes_controller_state(core, 0, 0x00)

    print(f"Warmup    : {args.warmup_frames} frames", flush=True)
    try:
        for _ in range(args.warmup_frames):
            core.step_frame_sync(1, max(1, int(args.step_timeout * 1000)))
    except MesenLoadError as exc:
        print(f"Warmup    : FAIL ({exc})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print(f"Warmup    : PASS NativeFrame={core.frame_count()}", flush=True)

    try:
        first = copy_nes_raw_frame(core)
    except MesenLoadError as exc:
        print(f"FrameCopy : FAIL ({exc})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    geometry_ok = (
        first.width == NES_FRAME_WIDTH
        and first.height == NES_FRAME_HEIGHT
        and len(first.pixels) == NES_FRAME_PIXEL_COUNT
    )
    range_ok = all(0 <= pixel <= 0x01FF for pixel in first.pixels)
    unique_count = len(set(first.pixels))
    content_ok = unique_count > 1
    count_ok = first.frame_count == core.frame_count()
    first_ok = geometry_ok and range_ok and content_ok and count_ok

    print(
        f"FrameCopy : {'PASS' if first_ok else 'FAIL'} "
        f"frame={first.frame_count} size={first.width}x{first.height} "
        f"pixels={len(first.pixels)} unique={unique_count} "
        f"raw_max=0x{max(first.pixels):03X} crc32={frame_crc32(first.pixels):08X}",
        flush=True,
    )
    if not first_ok:
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    before = core.frame_count()
    try:
        core.step_frame_sync(1, max(1, int(args.step_timeout * 1000)))
        second = copy_nes_raw_frame(core)
    except MesenLoadError as exc:
        print(f"FrameSync : FAIL ({exc})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    delta = (second.frame_count - first.frame_count) & 0xFFFFFFFF
    native_delta = (core.frame_count() - before) & 0xFFFFFFFF
    second_ok = delta == 1 and native_delta == 1 and second.frame_count == core.frame_count()
    print(
        f"FrameSync : {'PASS' if second_ok else 'FAIL'} "
        f"copy={first.frame_count}->{second.frame_count} delta={delta} "
        f"native_delta={native_delta} crc32={frame_crc32(second.pixels):08X}",
        flush=True,
    )
    if not second_ok:
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print("Framebuffer: PASS canonical raw NES PPU copy", flush=True)
    print("Format     : uint16 row-major; palette bits 0..5; emphasis bits 6..8", flush=True)
    print("Ownership  : caller-owned Python copy; no native framebuffer pointer escapes", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--home", str(args.home),
        "--warmup-frames", str(args.warmup_frames),
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
    failed = False
    assert process.stdout is not None
    try:
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                print(line, end="")
                if READY_MARKER in line:
                    ready = True
                    break
                if FAIL_MARKER in line:
                    failed = True
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
    if failed:
        print("WorkerExit: FORCED (failed probe contained)")
        print("Supervisor: FAIL")
        return 6

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
