#!/usr/bin/env python3
"""Machine-validate SMB1 LEVEL_COMPLETED through actual World 1-1 execution.

The probe uses a deterministic reflex baseline: hold RIGHT and issue a short A
pulse whenever Mario is grounded. It does not fabricate RAM state. Mesen remains
the execution authority and the event is accepted only when the real engine
enters PlayerEndLevel (0x05).
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
    NES_START,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    EpisodeAccumulator,
    EpisodeTermination,
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)

READY_MARKER = "WorkerReady: TERMINATE_FROM_SUPERVISOR"
FAIL_MARKER = "WorkerFail: TERMINATE_FROM_SUPERVISOR"
PLAYER_CONTROL = 0x08
FLAGPOLE_SLIDE = 0x04
PLAYER_END_LEVEL = 0x05
PLAYER_DEATH = 0x0B


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enter SMB1 World 1-1 and validate a real LEVEL_COMPLETED edge."
    )
    parser.add_argument("rom", type=Path)
    parser.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    parser.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    parser.add_argument("--title-max-frames", type=int, default=300)
    parser.add_argument("--entry-max-frames", type=int, default=360)
    parser.add_argument("--run-max-frames", type=int, default=7200)
    parser.add_argument("--jump-hold-frames", type=int, default=10)
    parser.add_argument("--jump-cooldown-frames", type=int, default=4)
    parser.add_argument("--step-timeout", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=180.0)
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
    for i in range(args.title_max_frames):
        try:
            step(core, args.step_timeout)
        except MesenLoadError as exc:
            fail(f"TitleWait : FAIL frame={i + 1} ({exc})")
        if read_smb1_state(core).is_title_menu:
            break
    else:
        fail("TitleMenu : FAIL")

    print(f"TitleMenu : PASS NativeFrame={core.frame_count()}", flush=True)
    set_nes_controller_state(core, 0, NES_START)
    step(core, args.step_timeout)
    set_nes_controller_state(core, 0, 0x00)
    step(core, args.step_timeout)

    for _ in range(args.entry_max_frames):
        step(core, args.step_timeout)
        state = read_smb1_state(core)
        if state.is_world_1_1_player_control:
            print(
                f"GameEntry : PASS NativeFrame={core.frame_count()} "
                f"X={state.player_absolute_x} Engine=0x{state.game_engine_subroutine:02X}",
                flush=True,
            )
            return state
    fail("GameEntry : FAIL")


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

    gameplay = enter_world_1_1(core, args)
    previous = observation_from_state(core.frame_count(), gameplay)
    episode = EpisodeAccumulator(previous)
    previous_engine = gameplay.game_engine_subroutine

    jump_hold = 0
    jump_cooldown = 0
    jump_count = 0
    level_events = 0
    saw_flagpole = False

    print(
        "Baseline  : adaptive RIGHT + grounded A pulses; waiting for real 1-1 completion",
        flush=True,
    )

    for i in range(args.run_max_frames):
        state_before = read_smb1_state(core)

        # Start a new jump only from the M0-validated grounded state, after A has
        # been released for a short cooldown. This creates repeated press edges
        # rather than holding A indefinitely.
        if (
            state_before.game_engine_subroutine == PLAYER_CONTROL
            and state_before.player_state == 0
            and jump_hold == 0
            and jump_cooldown == 0
        ):
            jump_hold = args.jump_hold_frames
            jump_count += 1

        buttons = NES_RIGHT
        if jump_hold > 0:
            buttons |= NES_A
            jump_hold -= 1
            if jump_hold == 0:
                jump_cooldown = args.jump_cooldown_frames
        elif jump_cooldown > 0:
            jump_cooldown -= 1

        set_nes_controller_state(core, 0, buttons)
        step(core, args.step_timeout)
        state = read_smb1_state(core)
        current = observation_from_state(core.frame_count(), state)
        events = derive_game_events(previous, current)
        episode.record(current, events)

        if state.game_engine_subroutine != previous_engine:
            print(
                f"EngineEdge: frame={current.native_frame_id} "
                f"0x{previous_engine:02X}->0x{state.game_engine_subroutine:02X} "
                f"X={state.player_absolute_x} Y=0x{state.player_y:02X} "
                f"State={state.player_state}",
                flush=True,
            )
            if state.game_engine_subroutine == FLAGPOLE_SLIDE:
                saw_flagpole = True
            previous_engine = state.game_engine_subroutine

        for event in events:
            if event.kind == GameEventType.DIED:
                set_nes_controller_state(core, 0, 0x00)
                result = episode.finish(EpisodeTermination.DEATH)
                fail(
                    "LevelRun  : FAIL death before completion "
                    f"frame={event.frame_id} max_x={result.max_x} jumps={jump_count}"
                )
            if event.kind == GameEventType.LEVEL_COMPLETED:
                level_events += 1
                print(
                    f"LevelEdge : PASS frame={event.frame_id} "
                    f"Engine=0x{state.game_engine_subroutine:02X} "
                    f"X={state.player_absolute_x}",
                    flush=True,
                )

        if level_events:
            set_nes_controller_state(core, 0, 0x00)
            duplicate_events = 0
            for _ in range(12):
                previous = current
                step(core, args.step_timeout)
                state = read_smb1_state(core)
                current = observation_from_state(core.frame_count(), state)
                more_events = derive_game_events(previous, current)
                episode.record(current, more_events)
                duplicate_events += sum(
                    event.kind == GameEventType.LEVEL_COMPLETED for event in more_events
                )
            if duplicate_events:
                fail(f"Duplicate  : FAIL extra_level_events={duplicate_events}")

            result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
            print("Duplicate  : PASS no repeated LEVEL_COMPLETED event", flush=True)
            print(
                f"Episode    : PASS termination={result.termination.value} "
                f"frames={result.elapsed_frames} start_x={result.start_x} "
                f"end_x={result.end_x} max_x={result.max_x} "
                f"progress={result.net_progress} events={result.event_count} "
                f"jumps={result.jump_events} landings={result.landing_events}",
                flush=True,
            )
            print(
                f"LevelEvent : PASS actual SMB1 execution emitted exactly one "
                f"LEVEL_COMPLETED edge; flagpole_seen={saw_flagpole}",
                flush=True,
            )
            print(READY_MARKER, flush=True)
            threading.Event().wait()

        if (i + 1) % 300 == 0:
            print(
                f"Progress  : frame={current.native_frame_id} run={i + 1}/{args.run_max_frames} "
                f"X={state.player_absolute_x} Engine=0x{state.game_engine_subroutine:02X} "
                f"State={state.player_state} jumps={jump_count}",
                flush=True,
            )

        previous = current

    set_nes_controller_state(core, 0, 0x00)
    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(
        f"LevelRun  : FAIL timeout frames={result.elapsed_frames} max_x={result.max_x} "
        f"Engine=0x{read_smb1_state(core).game_engine_subroutine:02X} "
        f"flagpole_seen={saw_flagpole} jumps={jump_count}"
    )


def supervisor(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(args.rom),
        "--dll", str(args.dll),
        "--home", str(args.home),
        "--title-max-frames", str(args.title_max_frames),
        "--entry-max-frames", str(args.entry_max_frames),
        "--run-max-frames", str(args.run_max_frames),
        "--jump-hold-frames", str(args.jump_hold_frames),
        "--jump-cooldown-frames", str(args.jump_cooldown_frames),
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
