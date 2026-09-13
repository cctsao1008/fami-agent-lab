#!/usr/bin/env python3
"""Run the V6 adaptive planner from an existing authoritative Mesen save state.

This is a regression harness, not a new planner. It exists so captured decision
states (for example, a pre-collapse or action-convergence checkpoint) can be
replayed directly without re-running SMB1 from the title screen every time.

The supplied .mss file remains authoritative. The harness loads it after ROM and
debugger initialization, then executes the same V6 coarse/precision decision
logic for a bounded number of decisions. It can optionally require a target X
for a focused escape/prevention regression.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    CandidateTerminal,
    EpisodeAccumulator,
    EpisodeTermination,
    observation_from_state,
    read_smb1_state,
)
from mesen_smb_checkpoint_planner import (
    CANDIDATES as COARSE_CANDIDATES,
    FLAGPOLE_SLIDE,
    commit_candidate,
    finish_flagpole,
    restore_checkpoint,
    save_checkpoint,
)
from mesen_smb_checkpoint_planner_v6 import (
    PRECISION_CANDIDATES,
    action_label,
    beam_search,
    capture_authoritative,
    evaluate_candidates,
    initialize_capture_dir,
    motion_summary,
    outcome_summary,
    precision_trigger,
    select_immediate,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay V6 directly from an authoritative SMB1 .mss checkpoint."
    )
    p.add_argument("rom", type=Path)
    p.add_argument("--state", type=Path, required=True, help="authoritative Mesen .mss checkpoint")
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    p.add_argument(
        "--work-state",
        type=Path,
        default=Path("build/checkpoints/smb1-v6-regression.mss"),
    )
    p.add_argument(
        "--capture-dir",
        type=Path,
        default=Path("build/checkpoints/v6-regression-captures"),
    )
    p.add_argument("--decisions", type=int, default=12)
    p.add_argument("--target-x", type=int, default=None)
    p.add_argument("--step-timeout", type=float, default=5.0)
    p.add_argument("--coarse-depth", type=int, default=5)
    p.add_argument("--coarse-width", type=int, default=8)
    p.add_argument("--precision-depth", type=int, default=4)
    p.add_argument("--precision-width", type=int, default=8)
    p.add_argument("--audit-interval", type=int, default=4)
    return p.parse_args()


def load_regression_state(core: MesenCore, path: Path) -> object:
    source = path.expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"regression state not found or empty: {source}")

    set_nes_controller_state(core, 0, 0x00)
    before_frame = core.frame_count()
    core.load_state_file(source)

    # Captured gameplay checkpoints are far beyond the freshly loaded ROM state.
    # Poll for the load to become visible, but do not advance emulation here.
    deadline = time.monotonic() + 3.0
    last = None
    while time.monotonic() < deadline:
        state = read_smb1_state(core)
        frame = core.frame_count()
        last = observation_from_state(frame, state)
        if frame != before_frame and state.oper_mode == 1:
            return last
        time.sleep(0.005)

    if last is None:
        raise RuntimeError("save-state load produced no readable SMB1 state")
    raise RuntimeError(
        "save-state load did not become visible within 3s: "
        f"frame={last.native_frame_id}, X={last.mario_x_abs}, "
        f"engine=0x{last.game_engine_subroutine:02X}"
    )


def finish_if_flagpole(core: MesenCore, current, episode: EpisodeAccumulator, timeout_s: float) -> int:
    if current.game_engine_subroutine == FLAGPOLE_SLIDE:
        current = finish_flagpole(core, current, episode, timeout_s)
    result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
    print("REGRESSION: LEVEL COMPLETE", flush=True)
    print(
        f"Result    : frame={current.native_frame_id} X={current.mario_x_abs} "
        f"max_x={result.max_x} progress={result.net_progress:+d}",
        flush=True,
    )
    return 0


def main() -> int:
    args = parse_args()
    if args.decisions <= 0:
        raise ValueError("--decisions must be > 0")

    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)
    print(f"Fixture   : {args.state.expanduser().resolve()}", flush=True)

    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)
    cfg = configure_standard_nes_controller(core, port=1)
    print(
        f"NesConfig : PASS (sizeof={ctypes.sizeof(cfg)} Port1.Type={cfg.Port1.Type})",
        flush=True,
    )
    if not core.load_rom(args.rom):
        print("LoadRom   : FAIL", flush=True)
        return 3
    print("LoadRom   : PASS", flush=True)
    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)

    current = load_regression_state(core, args.state)
    print(f"StateLoad : PASS | {motion_summary(current)} | frame={current.native_frame_id}", flush=True)

    episode = EpisodeAccumulator(current)
    initial_x = current.mario_x_abs
    work_state = args.work_state.expanduser().resolve()
    work_state.parent.mkdir(parents=True, exist_ok=True)
    captures = initialize_capture_dir(args.capture_dir)

    print("\n=== V6 Save-State Regression ===", flush=True)
    print(f"Start X        : {initial_x}", flush=True)
    print(f"Decision budget: {args.decisions}", flush=True)
    print(f"Target X       : {args.target_x if args.target_x is not None else 'not required'}", flush=True)
    print(f"Captures       : {captures}", flush=True)

    for decision in range(1, args.decisions + 1):
        current = observation_from_state(core.frame_count(), read_smb1_state(core))

        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            return finish_if_flagpole(core, current, episode, args.step_timeout)
        if args.target_x is not None and current.mario_x_abs >= args.target_x:
            print(
                f"REGRESSION: PASS target reached before decision {decision} | "
                f"X={current.mario_x_abs} target={args.target_x}",
                flush=True,
            )
            return 0

        frame, x, engine = save_checkpoint(core, work_state)
        capture_authoritative(work_state, captures, decision, frame, x)
        coarse = evaluate_candidates(
            core,
            COARSE_CANDIDATES,
            work_state,
            frame,
            x,
            engine,
            current,
            args.step_timeout,
            capture_dir=captures,
            decision=decision,
            mode="coarse",
        )
        trigger = precision_trigger(coarse)

        print("\n------------------------------------------------------------", flush=True)
        print(f"Regression decision #{decision:03d} | frame {frame} | Mario X={x}", flush=True)
        print("Coarse choices:", flush=True)
        for e in coarse:
            print(f"  {action_label(e.outcome.candidate.name):42s} -> {outcome_summary(e)}", flush=True)

        if trigger is not None:
            print(f"CONTROL ALERT : {trigger} -> PRECISION MODE", flush=True)
            precision = evaluate_candidates(
                core,
                PRECISION_CANDIDATES,
                work_state,
                frame,
                x,
                engine,
                current,
                args.step_timeout,
                capture_dir=captures,
                decision=decision,
                mode="precision",
            )
            for e in precision:
                print(f"  {action_label(e.outcome.candidate.name):42s} -> {outcome_summary(e)}", flush=True)
            root_name, logs = beam_search(
                core,
                PRECISION_CANDIDATES,
                work_state,
                frame,
                x,
                engine,
                current,
                args.step_timeout,
                args.precision_depth,
                args.precision_width,
                "precision",
            )
            for line in logs:
                print(line, flush=True)
            chosen = next(e for e in precision if e.outcome.candidate.name == root_name)
            mode = "PRECISION"
        elif decision % args.audit_interval == 0:
            root_name, logs = beam_search(
                core,
                COARSE_CANDIDATES,
                work_state,
                frame,
                x,
                engine,
                current,
                args.step_timeout,
                args.coarse_depth,
                args.coarse_width,
                "coarse",
            )
            for line in logs:
                print(line, flush=True)
            chosen = next(e for e in coarse if e.outcome.candidate.name == root_name)
            mode = "COARSE BEAM"
        else:
            chosen = select_immediate(coarse)
            mode = "COARSE FAST"

        print(f"Selected action: {action_label(chosen.outcome.candidate.name)}", flush=True)
        print(f"Decision mode  : {mode}", flush=True)

        restore_checkpoint(core, work_state, frame, x, engine)
        current, terminal, flag = commit_candidate(
            core,
            chosen.outcome.candidate,
            current,
            episode,
            args.step_timeout,
        )
        print(f"Committed state: frame={current.native_frame_id} | {motion_summary(current)}", flush=True)

        if terminal == CandidateTerminal.DEATH:
            result = episode.finish(EpisodeTermination.DEATH)
            print(
                f"REGRESSION: FAIL death | frame={current.native_frame_id} "
                f"X={current.mario_x_abs} max_x={result.max_x}",
                flush=True,
            )
            return 6
        if terminal == CandidateTerminal.LEVEL_COMPLETE or flag:
            return finish_if_flagpole(core, current, episode, args.step_timeout)
        if args.target_x is not None and current.mario_x_abs >= args.target_x:
            print(
                f"REGRESSION: PASS target reached | X={current.mario_x_abs} "
                f"target={args.target_x} decision={decision}",
                flush=True,
            )
            return 0

    result = episode.finish(EpisodeTermination.TIMEOUT)
    if args.target_x is not None:
        print(
            f"REGRESSION: FAIL target not reached | target={args.target_x} "
            f"end_x={current.mario_x_abs} max_x={result.max_x}",
            flush=True,
        )
        return 7

    print(
        f"REGRESSION: SURVIVED decision budget | end_x={current.mario_x_abs} "
        f"max_x={result.max_x} progress={result.net_progress:+d}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
