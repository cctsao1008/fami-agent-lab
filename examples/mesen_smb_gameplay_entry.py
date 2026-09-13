#!/usr/bin/env python3
"""Validate START -> World 1-1 and a game-state-aware SMB1 action rollout."""

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
    NES_START,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import read_smb1_state

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enter SMB1 World 1-1 and verify Mario movement from native RAM."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--title-max-frames", type=int, default=300)
    parser.add_argument("--entry-max-frames", type=int, default=360)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def step(core: MesenCore, timeout_s: float) -> None:
    core.step_frame_sync(1, max(1, int(timeout_s * 1000)))


def state_line(prefix: str, core: MesenCore) -> None:
    s = read_smb1_state(core)
    print(
        f"{prefix}: NativeFrame={core.frame_count()} SMBFrame=0x{s.frame_counter:02X} "
        f"OperMode={s.oper_mode} Task={s.oper_mode_task} Engine=0x{s.game_engine_subroutine:02X} "
        f"World={s.world + 1}-{s.level + 1} "
        f"MarioX={s.player_absolute_x} (page=0x{s.player_page:02X} x=0x{s.player_x:02X}) "
        f"Y=0x{s.player_y_high:02X}:0x{s.player_y:02X} State={s.player_state} "
        f"Joy=0x{s.saved_joypad1:02X}",
        flush=True,
    )


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

    title_state = None
    for i in range(args.title_max_frames):
        try:
            step(core, args.step_timeout)
        except MesenLoadError as exc:
            print(f"TitleWait : FAIL frame={i + 1} ({exc})", flush=True)
            print(FAIL_MARKER, flush=True)
            threading.Event().wait()
        candidate = read_smb1_state(core)
        if candidate.is_title_menu:
            title_state = candidate
            break

    if title_state is None:
        state_line("TitleWait : FAIL", core)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    state_line("TitleMenu : PASS", core)

    # Native START bit is 0x08; SMB's software-side SavedJoypad representation
    # is expected to expose Start as 0x10 after serial polling.
    set_nes_controller_state(core, 0, NES_START)
    step(core, args.step_timeout)
    start_seen = read_smb1_state(core)
    print(
        f"START     : Native=0x{NES_START:02X} SMBJoy=0x{start_seen.saved_joypad1:02X}",
        flush=True,
    )
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
        state_line("GameEntry : FAIL", core)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()
    state_line("GameEntry : PASS", core)

    baseline_x = gameplay.player_absolute_x
    min_y = gameplay.player_y
    max_y = gameplay.player_y
    non_ground_frames = 0
    samples = []

    sequence = [
        ("RIGHT", NES_RIGHT, 60),
        ("RIGHT+A", NES_RIGHT | NES_A, 10),
        ("RELEASE", 0x00, 2),
    ]

    rollout_index = 0
    for action, buttons, count in sequence:
        set_nes_controller_state(core, 0, buttons)
        for _ in range(count):
            rollout_index += 1
            step(core, args.step_timeout)
            s = read_smb1_state(core)
            samples.append(s)
            min_y = min(min_y, s.player_y)
            max_y = max(max_y, s.player_y)
            if s.player_state != 0:
                non_ground_frames += 1
            if rollout_index in (1, 60, 61, 70, 71, 72):
                print(
                    f"Frame {rollout_index:03d}: action={action:<7} "
                    f"NativeFrame={core.frame_count()} X={s.player_absolute_x} "
                    f"Y=0x{s.player_y:02X} State={s.player_state} Joy=0x{s.saved_joypad1:02X}",
                    flush=True,
                )

    final = samples[-1]
    x_delta = final.player_absolute_x - baseline_x
    moved_right = x_delta > 0
    jump_observed = non_ground_frames > 0 and any(s.player_state in (1, 2) for s in samples[60:])
    still_in_1_1 = final.oper_mode == 1 and final.world == 0 and final.level == 0
    release_seen = final.saved_joypad1 == 0

    print(
        f"Movement  : {'PASS' if moved_right else 'FAIL'} "
        f"X={baseline_x}->{final.player_absolute_x} delta={x_delta}",
        flush=True,
    )
    print(
        f"Jump      : {'PASS' if jump_observed else 'FAIL'} "
        f"non_ground_frames={non_ground_frames} Yrange=0x{min_y:02X}..0x{max_y:02X}",
        flush=True,
    )
    print(
        f"Release   : {'PASS' if release_seen else 'FAIL'} SMBJoy=0x{final.saved_joypad1:02X}",
        flush=True,
    )

    if not (moved_right and jump_observed and still_in_1_1 and release_seen):
        state_line("Gameplay  : FAIL", core)
        print(FAIL_MARKER, flush=True)
        threading.Event().wait()

    print("Gameplay  : PASS START -> World 1-1 -> Mario movement/jump witness", flush=True)
    print("Sequence  : RIGHT x60 -> RIGHT+A x10 -> RELEASE x2", flush=True)
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
