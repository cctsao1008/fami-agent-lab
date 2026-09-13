#!/usr/bin/env python3
"""Validate native frame stepping and deterministic NES controller injection.

The hard M0 witnesses in this probe are emulator-owned state:

- FamiPixelStepFrame(1) must advance the native frame counter by exactly one.
- FamiPixelGetNesControllerState() must equal the byte injected through the
  fork-side IInputProvider.

SMB RAM is printed as a secondary observation only. It is intentionally not a
pass/fail condition here because boot/title-screen ownership of those bytes is
game-phase dependent.
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
    MesenLoadError,
    NES_A,
    NES_RIGHT,
    available_input_overrides,
    configure_standard_nes_controller,
    get_nes_controller_state,
    read_nes_cpu_memory,
    set_nes_controller_state,
)

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"

SMB_FRAME_COUNTER = 0x0009
SMB_RAW_JOYPAD1_BITS = 0x074A


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify synchronous frames plus native IInputProvider controller delivery."
    )
    parser.add_argument("rom", type=Path, help="Path to a local SMB1 NES ROM")
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--controller", type=int, default=0)
    parser.add_argument("--warmup-frames", type=int, default=120)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def step_exactly_one(core: MesenCore, timeout: float) -> tuple[int, int, float]:
    before = core.frame_count()
    start = time.perf_counter()
    core.step_frame_sync(1, max(1, int(timeout * 1000)))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    after = core.frame_count()
    return before, after, elapsed_ms


def run_one_frame(
    core: MesenCore,
    port: int,
    buttons: int,
    label: str,
    frame: int,
    timeout: float,
) -> bool:
    set_nes_controller_state(core, port, buttons)
    before_smb = read_nes_cpu_memory(core, SMB_FRAME_COUNTER)

    try:
        before_native, after_native, elapsed_ms = step_exactly_one(core, timeout)
    except MesenLoadError as exc:
        print(
            f"Frame {frame:03d}: FAIL action={label:<7} native-step={exc}",
            flush=True,
        )
        return False

    controller = get_nes_controller_state(core, port)
    after_smb = read_nes_cpu_memory(core, SMB_FRAME_COUNTER)
    raw = read_nes_cpu_memory(core, SMB_RAW_JOYPAD1_BITS)
    stopped = core.is_execution_stopped()
    native_delta = (after_native - before_native) & 0xFFFFFFFF

    frame_ok = native_delta == 1
    controller_ok = controller == buttons
    stopped_ok = stopped
    ok = frame_ok and controller_ok and stopped_ok

    print(
        f"Frame {frame:03d}: {'PASS' if ok else 'FAIL'} action={label:<7} "
        f"NativeFrame={before_native}->{after_native} delta={native_delta} "
        f"Controller=0x{controller:02X} expected=0x{buttons:02X} "
        f"SMBFrame=0x{before_smb:02X}->0x{after_smb:02X} "
        f"SMBRaw=0x{raw:02X} stopped={stopped} elapsed={elapsed_ms:.3f} ms",
        flush=True,
    )
    return ok


def worker(args: argparse.Namespace) -> None:
    if args.warmup_frames < 0:
        raise SystemExit("--warmup-frames must be >= 0")

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
    print(f"NativeFrame: {core.frame_count()}", flush=True)

    slots = available_input_overrides(core)
    print("InputSlots: " + " ".join(f"{i}={'yes' if v else 'no'}" for i, v in enumerate(slots)), flush=True)
    if not 0 <= args.controller <= 1 or not slots[args.controller]:
        print(f"Controller: FAIL (slot {args.controller} unavailable)", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print(f"Controller: PASS (NES port {args.controller})", flush=True)

    # Register the native provider with an explicit released state, then move
    # through boot/title frames before inspecting SMB-specific RAM.
    set_nes_controller_state(core, args.controller, 0x00)
    print(f"Warmup    : {args.warmup_frames} frames (RELEASE)", flush=True)
    try:
        for _ in range(args.warmup_frames):
            before, after, _elapsed = step_exactly_one(core, args.step_timeout)
            if ((after - before) & 0xFFFFFFFF) != 1:
                raise MesenLoadError(f"warmup frame delta was {after - before}")
    except MesenLoadError as exc:
        print(f"Warmup    : FAIL ({exc})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print(
        f"Warmup    : PASS NativeFrame={core.frame_count()} Controller=0x{get_nes_controller_state(core, args.controller):02X}",
        flush=True,
    )
    print("Witness   : native frame delta + actual emulated NES controller byte", flush=True)

    sequence = [
        (NES_RIGHT, "RIGHT"),
        (NES_RIGHT, "RIGHT"),
        (NES_RIGHT, "RIGHT"),
        (NES_RIGHT, "RIGHT"),
        (NES_RIGHT | NES_A, "RIGHT+A"),
        (NES_RIGHT | NES_A, "RIGHT+A"),
        (0x00, "RELEASE"),
        (0x00, "RELEASE"),
    ]

    passed = 0
    for frame, (buttons, label) in enumerate(sequence, start=1):
        if run_one_frame(core, args.controller, buttons, label, frame, args.step_timeout):
            passed += 1
        else:
            break

    if passed != len(sequence):
        print(f"NativeInputWitness: FAIL ({passed}/{len(sequence)})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print(f"NativeInputWitness: PASS ({passed}/{len(sequence)})", flush=True)
    print("NativePath        : Python -> IInputProvider -> NES controller -> frame -> Python", flush=True)
    print("SMB RAM            : secondary observation only in this probe", flush=True)
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
