#!/usr/bin/env python3
"""Machine-validate Mesen state-file checkpoints for planning/search.

This probe establishes the prerequisite for checkpointed action search:
enter SMB1 World 1-1, advance to a known state, save it, move forward, reload
the checkpoint, and verify that authoritative SMB1 state returns to the saved
position. No RAM state is fabricated.
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
    NES_RIGHT,
    NES_START,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import read_smb1_state

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate save-state file roundtrip during SMB1 gameplay."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("build/checkpoints/smb1-world1-1-roundtrip.mss"),
    )
    parser.add_argument("--advance-before-save", type=int, default=120)
    parser.add_argument("--advance-after-save", type=int, default=80)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def step(core: MesenCore, timeout_s: float) -> None:
    core.step_frame_sync(1, max(1, int(timeout_s * 1000)))


def fail(message: str) -> None:
    print(message, flush=True)
    print(FAIL_MARKER, flush=True)
    threading.Event().wait()


def enter_world_1_1(core: MesenCore, args: argparse.Namespace):
    set_nes_controller_state(core, 0, 0x00)
    for _ in range(300):
        step(core, args.step_timeout)
        state = read_smb1_state(core)
        if state.is_title_menu:
            break
    else:
        fail("TitleMenu : FAIL")

    print(f"TitleMenu : PASS NativeFrame={core.frame_count()}", flush=True)
    set_nes_controller_state(core, 0, NES_START)
    step(core, args.step_timeout)
    set_nes_controller_state(core, 0, 0x00)
    step(core, args.step_timeout)

    for _ in range(360):
        step(core, args.step_timeout)
        state = read_smb1_state(core)
        if state.is_world_1_1_player_control:
            print(
                f"GameEntry : PASS NativeFrame={core.frame_count()} X={state.player_absolute_x}",
                flush=True,
            )
            return state
    fail("GameEntry : FAIL")


def wait_for_file(path: Path, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return True
        time.sleep(0.01)
    return False


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
        fail("LoadRom   : FAIL")
    print("LoadRom   : PASS", flush=True)
    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)

    enter_world_1_1(core, args)

    set_nes_controller_state(core, 0, NES_RIGHT)
    for _ in range(args.advance_before_save):
        step(core, args.step_timeout)
    set_nes_controller_state(core, 0, 0x00)

    saved_state = read_smb1_state(core)
    saved_frame = core.frame_count()
    state_file = args.state_file.expanduser().resolve()
    if state_file.exists():
        state_file.unlink()
    core.save_state_file(state_file)
    if not wait_for_file(state_file):
        fail(f"SaveState : FAIL file not created: {state_file}")
    print(
        f"SaveState : PASS frame={saved_frame} X={saved_state.player_absolute_x} "
        f"bytes={state_file.stat().st_size}",
        flush=True,
    )

    set_nes_controller_state(core, 0, NES_RIGHT)
    for _ in range(args.advance_after_save):
        step(core, args.step_timeout)
    set_nes_controller_state(core, 0, 0x00)
    advanced_state = read_smb1_state(core)
    advanced_frame = core.frame_count()

    if advanced_state.player_absolute_x <= saved_state.player_absolute_x:
        fail(
            f"Advance   : FAIL saved_x={saved_state.player_absolute_x} "
            f"advanced_x={advanced_state.player_absolute_x}"
        )
    print(
        f"Advance   : PASS frame={advanced_frame} X={advanced_state.player_absolute_x} "
        f"delta={advanced_state.player_absolute_x - saved_state.player_absolute_x}",
        flush=True,
    )

    core.load_state_file(state_file)

    restored_state = None
    restored_frame = None
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        candidate = read_smb1_state(core)
        candidate_frame = core.frame_count()
        if candidate.player_absolute_x == saved_state.player_absolute_x:
            restored_state = candidate
            restored_frame = candidate_frame
            break
        time.sleep(0.01)

    if restored_state is None or restored_frame is None:
        current = read_smb1_state(core)
        fail(
            f"LoadState : FAIL expected_x={saved_state.player_absolute_x} "
            f"actual_x={current.player_absolute_x} frame={core.frame_count()}"
        )

    if restored_state.game_engine_subroutine != saved_state.game_engine_subroutine:
        fail(
            f"Restore   : FAIL engine saved=0x{saved_state.game_engine_subroutine:02X} "
            f"restored=0x{restored_state.game_engine_subroutine:02X}"
        )

    print(
        f"LoadState : PASS frame={restored_frame} X={restored_state.player_absolute_x} "
        f"Engine=0x{restored_state.game_engine_subroutine:02X}",
        flush=True,
    )

    # Prove execution can continue from the restored checkpoint.
    set_nes_controller_state(core, 0, NES_RIGHT)
    step(core, args.step_timeout)
    set_nes_controller_state(core, 0, 0x00)
    resumed_state = read_smb1_state(core)
    print(
        f"Resume    : PASS frame={core.frame_count()} X={resumed_state.player_absolute_x}",
        flush=True,
    )
    print("Checkpoint: PASS save/load/continue roundtrip", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--home", str(args.home),
        "--state-file", str(args.state_file),
        "--advance-before-save", str(args.advance_before_save),
        "--advance-after-save", str(args.advance_after_save),
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

    ready = False
    failed = False
    deadline = time.monotonic() + args.timeout
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
