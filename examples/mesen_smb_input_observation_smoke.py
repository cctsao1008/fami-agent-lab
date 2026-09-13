#!/usr/bin/env python3
"""Verify SMB receives frame-aligned controller input through native RAM observation.

The generic Mesen adapter supplies action + memory access. The SMB-specific
address/bit interpretation lives only in this example.
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

# SMB1 symbols / controller bit masks from the byte-exact disassembly family.
SMB_SAVED_JOYPAD_BITS = 0x06FC
SMB_RIGHT = 0x01
SMB_A = 0x80


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the M0 action sequence and verify SMB SavedJoypadBits each frame."
    )
    parser.add_argument("rom", type=Path, help="Path to a local SMB1 NES ROM")
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--controller", type=int, default=0)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def wait_for_stop(core: MesenCore, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if core.is_execution_stopped():
            return True
        time.sleep(0.001)
    return False


def run_segment(core, controller, state, label, expected, first_frame, count, timeout):
    set_input_override(core, controller, state)
    failures = 0
    last_frame = first_frame - 1
    for frame in range(first_frame, first_frame + count):
        core.step_ppu_frame(1)
        if not wait_for_stop(core, timeout):
            print(f"Frame {frame:03d}: FAIL action={label} (no stopped state)", flush=True)
            raise SystemExit(4)
        observed = read_nes_cpu_memory(core, SMB_SAVED_JOYPAD_BITS)
        ok = observed == expected
        if not ok:
            failures += 1
        print(
            f"Frame {frame:03d}: {'PASS' if ok else 'FAIL'} "
            f"action={label:<7} SavedJoypadBits=0x{observed:02X} expected=0x{expected:02X}",
            flush=True,
        )
        last_frame = frame
    return last_frame, failures


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
        raise SystemExit(1)
    print("LoadRom   : PASS", flush=True)

    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)
    slots = available_input_overrides(core)
    print("InputSlots: " + " ".join(f"{i}={'yes' if v else 'no'}" for i, v in enumerate(slots)), flush=True)
    if not 0 <= args.controller < 8 or not slots[args.controller]:
        print(f"Controller: FAIL (slot {args.controller} unavailable)", flush=True)
        raise SystemExit(5)
    print(f"Controller: PASS (slot {args.controller})", flush=True)
    print(f"Observation: SMB SavedJoypadBits @ $06FC", flush=True)

    frame = 1
    failures = 0
    last, bad = run_segment(core, args.controller, right_state(), "RIGHT", SMB_RIGHT, frame, 60, args.step_timeout)
    failures += bad
    frame = last + 1
    last, bad = run_segment(core, args.controller, right_state(jump=True), "RIGHT+A", SMB_RIGHT | SMB_A, frame, 10, args.step_timeout)
    failures += bad
    frame = last + 1
    last, bad = run_segment(core, args.controller, released_state(), "RELEASE", 0x00, frame, 2, args.step_timeout)
    failures += bad

    if failures:
        print(f"InputObservation: FAIL ({failures} mismatched frames)", flush=True)
        raise SystemExit(6)

    print("InputObservation: PASS (72/72 frame-aligned native RAM observations)", flush=True)
    print("Sequence        : RIGHT x60 -> RIGHT+A x10 -> RELEASE x2", flush=True)
    print("NativePath      : Python -> Mesen input override -> SMB joypad RAM -> Python", flush=True)
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
