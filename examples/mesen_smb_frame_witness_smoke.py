#!/usr/bin/env python3
"""Witness real SMB frame advancement and raw controller delivery.

This probe avoids two ambiguous signals from earlier M0 experiments:

- IsExecutionStopped()==True can be stale at an already-stopped boundary.
- SavedJoypadBits ($06FC) is gameplay-specific and is not a valid boot/title
  screen input witness.

Instead, every requested PPU-frame step must change SMB's FrameCounter ($0009),
and controller delivery is observed through RawJoypad1Bits ($074A).
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import threading
import time
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    available_input_overrides,
    configure_standard_nes_controller,
    read_nes_cpu_memory,
    released_state,
    right_state,
    set_input_override,
)

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"

SMB_FRAME_COUNTER = 0x0009
SMB_RAW_JOYPAD1_BITS = 0x074A
SMB_RIGHT = 0x01
SMB_A = 0x80


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify physical SMB frame advancement plus raw joypad delivery."
    )
    parser.add_argument("rom", type=Path, help="Path to a local SMB1 NES ROM")
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--controller", type=int, default=0)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def wait_for_frame_counter_change(core: MesenCore, previous: int, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = read_nes_cpu_memory(core, SMB_FRAME_COUNTER)
        if current != previous:
            return current
        time.sleep(0.0005)
    return None


def run_one_frame(
    core: MesenCore,
    controller: int,
    state,
    label: str,
    expected_raw: int,
    frame: int,
    timeout: float,
) -> bool:
    before_counter = read_nes_cpu_memory(core, SMB_FRAME_COUNTER)
    before_stopped = core.is_execution_stopped()
    set_input_override(core, controller, state)

    start = time.perf_counter()
    core.step_ppu_frame(1)
    after_counter = wait_for_frame_counter_change(core, before_counter, timeout)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    if after_counter is None:
        raw = read_nes_cpu_memory(core, SMB_RAW_JOYPAD1_BITS)
        print(
            f"Frame {frame:03d}: FAIL action={label:<7} "
            f"FrameCounter=0x{before_counter:02X}->UNCHANGED "
            f"RawJoypad=0x{raw:02X} stopped_before={before_stopped} "
            f"stopped_now={core.is_execution_stopped()} elapsed={elapsed_ms:.3f} ms",
            flush=True,
        )
        return False

    # Let the requested step settle at its debugger boundary before sampling the
    # final raw-input byte. The frame-counter transition itself is the primary
    # proof that machine time advanced.
    settle_deadline = time.monotonic() + timeout
    while time.monotonic() < settle_deadline and not core.is_execution_stopped():
        time.sleep(0.0005)

    raw = read_nes_cpu_memory(core, SMB_RAW_JOYPAD1_BITS)
    raw_ok = raw == expected_raw
    stopped = core.is_execution_stopped()
    ok = raw_ok and stopped
    print(
        f"Frame {frame:03d}: {'PASS' if ok else 'FAIL'} action={label:<7} "
        f"FrameCounter=0x{before_counter:02X}->0x{after_counter:02X} "
        f"RawJoypad=0x{raw:02X} expected=0x{expected_raw:02X} "
        f"stopped={stopped} elapsed={elapsed_ms:.3f} ms",
        flush=True,
    )
    return ok


def worker(args: argparse.Namespace) -> None:
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
    slots = available_input_overrides(core)
    print("InputSlots: " + " ".join(f"{i}={'yes' if v else 'no'}" for i, v in enumerate(slots)), flush=True)
    if not 0 <= args.controller < 8 or not slots[args.controller]:
        print(f"Controller: FAIL (slot {args.controller} unavailable)", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print(f"Controller: PASS (slot {args.controller})", flush=True)
    print("Witness   : FrameCounter=$0009 RawJoypad1Bits=$074A", flush=True)

    sequence = [
        (right_state(), "RIGHT", SMB_RIGHT),
        (right_state(), "RIGHT", SMB_RIGHT),
        (right_state(), "RIGHT", SMB_RIGHT),
        (right_state(), "RIGHT", SMB_RIGHT),
        (right_state(jump=True), "RIGHT+A", SMB_RIGHT | SMB_A),
        (right_state(jump=True), "RIGHT+A", SMB_RIGHT | SMB_A),
        (released_state(), "RELEASE", 0x00),
        (released_state(), "RELEASE", 0x00),
    ]

    passed = 0
    for frame, (state, label, expected) in enumerate(sequence, start=1):
        if run_one_frame(core, args.controller, state, label, expected, frame, args.step_timeout):
            passed += 1
        else:
            break

    if passed != len(sequence):
        print(f"FrameWitness: FAIL ({passed}/{len(sequence)})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print(f"FrameWitness: PASS ({passed}/{len(sequence)})", flush=True)
    print("NativePath  : action -> Mesen -> SMB raw joypad + frame counter -> Python", flush=True)
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
