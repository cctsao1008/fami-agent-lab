#!/usr/bin/env python3
"""Machine-validate SMB1 DIED event against actual World 1-1 execution."""

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
    NES_START,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"
PLAYER_DEATH_ROUTINE = 0x0B


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enter SMB1 World 1-1, stand still, and validate actual DIED event entry."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--title-max-frames", type=int, default=300)
    parser.add_argument("--entry-max-frames", type=int, default=360)
    parser.add_argument("--death-max-frames", type=int, default=900)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def step(core: MesenCore, timeout_s: float) -> None:
    core.step_frame_sync(1, max(1, int(timeout_s * 1000)))


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
    set_nes_controller_state(core, 0, 0x00)

    for i in range(args.title_max_frames):
        try:
            step(core, args.step_timeout)
        except MesenLoadError as exc:
            print(f"TitleWait : FAIL frame={i + 1} ({exc})", flush=True)
            print(FAIL_MARKER, flush=True)
            threading.Event().wait()
        if read_smb1_state(core).is_title_menu:
            break
    else:
        print("TitleMenu : FAIL", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print(f"TitleMenu : PASS NativeFrame={core.frame_count()}", flush=True)

    set_nes_controller_state(core, 0, NES_START)
    step(core, args.step_timeout)
    set_nes_controller_state(core, 0, 0x00)
    step(core, args.step_timeout)

    gameplay = None
    for _ in range(args.entry_max_frames):
        step(core, args.step_timeout)
        candidate = read_smb1_state(core)
        if candidate.is_world_1_1_player_control:
            gameplay = candidate
            break

    if gameplay is None:
        print("GameEntry : FAIL", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print(
        f"GameEntry : PASS NativeFrame={core.frame_count()} X={gameplay.player_absolute_x} "
        f"Engine=0x{gameplay.game_engine_subroutine:02X}",
        flush=True,
    )

    previous = observation_from_state(core.frame_count(), gameplay)
    set_nes_controller_state(core, 0, 0x00)

    death_events = 0
    death_frame = None
    for i in range(args.death_max_frames):
        step(core, args.step_timeout)
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)
        events = derive_game_events(previous, current)
        for event in events:
            if event.kind == GameEventType.DIED:
                death_events += 1
                death_frame = event.frame_id
                print(
                    f"DeathEdge : PASS frame={event.frame_id} "
                    f"Engine=0x{state.game_engine_subroutine:02X} X={state.player_absolute_x} "
                    f"Y=0x{state.player_y:02X}",
                    flush=True,
                )
        if death_events:
            # Step a few more frames to prove edge-triggered duplicate suppression.
            duplicate_window = 0
            duplicate_events = 0
            while duplicate_window < 12:
                duplicate_window += 1
                previous = current
                step(core, args.step_timeout)
                state = read_smb1_state(core)
                current = observation_from_state(core.frame_count(), state)
                for event in derive_game_events(previous, current):
                    if event.kind == GameEventType.DIED:
                        duplicate_events += 1
            if duplicate_events != 0:
                print(
                    f"Duplicate  : FAIL extra_died_events={duplicate_events}",
                    flush=True,
                )
                print(FAIL_MARKER, flush=True)
                threading.Event().wait()
            print("Duplicate  : PASS no repeated DIED event", flush=True)
            break
        previous = current
    else:
        final = read_smb1_state(core)
        print(
            f"DeathEdge : FAIL no DIED event within {args.death_max_frames} frames; "
            f"Engine=0x{final.game_engine_subroutine:02X} X={final.player_absolute_x}",
            flush=True,
        )
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    if death_events != 1 or death_frame is None:
        print(f"DeathEvent: FAIL count={death_events}", flush=True)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    final = read_smb1_state(core)
    if final.game_engine_subroutine != PLAYER_DEATH_ROUTINE:
        print(
            f"DeathState: FAIL expected Engine=0x{PLAYER_DEATH_ROUTINE:02X} "
            f"got=0x{final.game_engine_subroutine:02X}",
            flush=True,
        )
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print("DeathEvent: PASS actual SMB1 execution emitted exactly one DIED edge", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--home", str(args.home),
        "--title-max-frames", str(args.title_max_frames),
        "--entry-max-frames", str(args.entry_max_frames),
        "--death-max-frames", str(args.death_max_frames),
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
