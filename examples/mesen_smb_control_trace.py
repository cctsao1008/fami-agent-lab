#!/usr/bin/env python3
"""Emit the exact M0 SMB control trace as a validated CSV artifact.

Sequence:
    RIGHT x60 -> RIGHT+A x10 -> RELEASE x2

Each row is emitted only after one synchronous native NES frame completes. The
trace is considered valid only if all of the following hold for every frame:

- native frame delta == 1,
- actual emulated NES controller byte equals the requested native byte,
- SMB RawJoypad1Bits reflects the same action using SMB's own bit layout.

The CSV is written under build/traces/ by default and is intentionally not a
source artifact.
"""

from __future__ import annotations

import argparse
import csv
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
SMB_RIGHT = 0x01
SMB_A = 0x80


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit and validate the exact 72-frame M0 SMB control trace."
    )
    parser.add_argument("rom", type=Path, help="Path to a local SMB1 NES ROM")
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--controller", type=int, default=0)
    parser.add_argument("--warmup-frames", type=int, default=120)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/traces/m0_smb_control_trace.csv"),
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def step_exactly_one(core: MesenCore, timeout: float) -> tuple[int, int, float]:
    before = core.frame_count()
    start = time.perf_counter()
    core.step_frame_sync(1, max(1, int(timeout * 1000)))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    after = core.frame_count()
    return before, after, elapsed_ms


def m0_sequence() -> list[tuple[int, str, int]]:
    return (
        [(NES_RIGHT, "RIGHT", SMB_RIGHT)] * 60
        + [(NES_RIGHT | NES_A, "RIGHT+A", SMB_RIGHT | SMB_A)] * 10
        + [(0x00, "RELEASE", 0x00)] * 2
    )


def worker(args: argparse.Namespace) -> None:
    if args.warmup_frames < 0:
        raise SystemExit("--warmup-frames must be >= 0")

    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)
    print(f"Trace     : {args.output.expanduser().resolve()}", flush=True)

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
    if not 0 <= args.controller <= 1 or not slots[args.controller]:
        print(f"Controller: FAIL (slot {args.controller} unavailable)", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print(f"Controller: PASS (NES port {args.controller})", flush=True)

    set_nes_controller_state(core, args.controller, 0x00)
    print(f"Warmup    : {args.warmup_frames} frames (RELEASE)", flush=True)
    try:
        for _ in range(args.warmup_frames):
            before, after, _ = step_exactly_one(core, args.step_timeout)
            if ((after - before) & 0xFFFFFFFF) != 1:
                raise MesenLoadError(f"warmup frame delta was {(after - before) & 0xFFFFFFFF}")
    except MesenLoadError as exc:
        print(f"Warmup    : FAIL ({exc})", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    print(f"Warmup    : PASS NativeFrame={core.frame_count()}", flush=True)

    sequence = m0_sequence()
    rows: list[dict[str, object]] = []
    mismatches = 0

    for frame_index, (buttons, label, expected_smb_raw) in enumerate(sequence, start=1):
        set_nes_controller_state(core, args.controller, buttons)
        smb_frame_before = read_nes_cpu_memory(core, SMB_FRAME_COUNTER)

        try:
            native_before, native_after, elapsed_ms = step_exactly_one(core, args.step_timeout)
        except MesenLoadError as exc:
            print(f"Frame {frame_index:03d}: FAIL action={label} native-step={exc}", flush=True)
            mismatches += 1
            break

        controller = get_nes_controller_state(core, args.controller)
        smb_frame_after = read_nes_cpu_memory(core, SMB_FRAME_COUNTER)
        smb_raw = read_nes_cpu_memory(core, SMB_RAW_JOYPAD1_BITS)
        native_delta = (native_after - native_before) & 0xFFFFFFFF
        stopped = core.is_execution_stopped()

        frame_ok = native_delta == 1
        controller_ok = controller == buttons
        smb_raw_ok = smb_raw == expected_smb_raw
        stopped_ok = stopped
        ok = frame_ok and controller_ok and smb_raw_ok and stopped_ok

        rows.append(
            {
                "frame_index": frame_index,
                "native_frame_before": native_before,
                "native_frame_after": native_after,
                "native_frame_delta": native_delta,
                "action": label,
                "native_buttons_hex": f"0x{buttons:02X}",
                "controller_hex": f"0x{controller:02X}",
                "smb_raw_expected_hex": f"0x{expected_smb_raw:02X}",
                "smb_raw_joypad_hex": f"0x{smb_raw:02X}",
                "smb_frame_before_hex": f"0x{smb_frame_before:02X}",
                "smb_frame_after_hex": f"0x{smb_frame_after:02X}",
                "execution_stopped": int(stopped),
                "elapsed_ms": f"{elapsed_ms:.3f}",
                "pass": int(ok),
            }
        )

        print(
            f"Frame {frame_index:03d}: {'PASS' if ok else 'FAIL'} action={label:<7} "
            f"NativeFrame={native_before}->{native_after} delta={native_delta} "
            f"Controller=0x{controller:02X}/0x{buttons:02X} "
            f"SMBRaw=0x{smb_raw:02X}/0x{expected_smb_raw:02X} "
            f"SMBFrame=0x{smb_frame_before:02X}->0x{smb_frame_after:02X}",
            flush=True,
        )

        if not ok:
            mismatches += 1
            break

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "frame_index",
        "native_frame_before",
        "native_frame_after",
        "native_frame_delta",
        "action",
        "native_buttons_hex",
        "controller_hex",
        "smb_raw_expected_hex",
        "smb_raw_joypad_hex",
        "smb_frame_before_hex",
        "smb_frame_after_hex",
        "execution_stopped",
        "elapsed_ms",
        "pass",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if mismatches or len(rows) != len(sequence):
        print(
            f"ControlTrace: FAIL rows={len(rows)}/{len(sequence)} mismatches={mismatches}",
            flush=True,
        )
        print(f"CSV       : {output}", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print("ControlTrace: PASS (72/72)", flush=True)
    print("Sequence    : RIGHT x60 -> RIGHT+A x10 -> RELEASE x2", flush=True)
    print(f"CSV         : {output}", flush=True)
    print("Columns     : frame_id + action + native controller + SMB raw/frame observation", flush=True)
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
        "--output", str(args.output),
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
        print("WorkerExit: FORCED (failed trace contained)")
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
