#!/usr/bin/env python3
"""B-aware controller-sequence planner for SMB1 World 1-1.

V7 turns the official SMB1 control semantics into the planner action model:
horizontal intent, A hold, B/run state, and short frame-explicit durations are
independent control dimensions. It also folds regression replay into the same
entry point so a captured V5/V6 report ZIP can be tested without manually
extracting or renaming save states.

Mesen remains machine authority. Candidate rollouts are counterfactual and only
the first selected root command is committed to the authoritative episode.
"""

from __future__ import annotations

import argparse
import ctypes
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller, set_nes_controller_state
from fami_pixel.games.smb1 import (
    ActionCommand,
    CandidateTerminal,
    EpisodeAccumulator,
    EpisodeTermination,
    PlanCandidate,
    Smb1Action,
    observation_from_state,
    read_smb1_state,
)
from mesen_smb_checkpoint_planner import (
    FLAGPOLE_SLIDE,
    commit_candidate,
    enter_world_1_1,
    finish_flagpole,
    restore_checkpoint,
    save_checkpoint,
)
import mesen_smb_checkpoint_planner_v6 as v6


COARSE_CANDIDATES = (
    PlanCandidate("cruise", (ActionCommand(Smb1Action.RIGHT, 30),)),
    PlanCandidate("tap_jump", (ActionCommand(Smb1Action.RIGHT_A, 6), ActionCommand(Smb1Action.RIGHT, 24))),
    PlanCandidate("medium_jump", (ActionCommand(Smb1Action.RIGHT_A, 14), ActionCommand(Smb1Action.RIGHT, 16))),
    PlanCandidate("long_jump", (ActionCommand(Smb1Action.RIGHT_A, 24), ActionCommand(Smb1Action.RIGHT, 6))),
    PlanCandidate("run", (ActionCommand(Smb1Action.RIGHT_B, 30),)),
    PlanCandidate("run_tap_jump", (ActionCommand(Smb1Action.RIGHT_A_B, 6), ActionCommand(Smb1Action.RIGHT_B, 24))),
    PlanCandidate("run_medium_jump", (ActionCommand(Smb1Action.RIGHT_A_B, 14), ActionCommand(Smb1Action.RIGHT_B, 16))),
    PlanCandidate("run_long_jump", (ActionCommand(Smb1Action.RIGHT_A_B, 24), ActionCommand(Smb1Action.RIGHT_B, 6))),
)


def _precision_candidates() -> tuple[PlanCandidate, ...]:
    specs = (
        ("noop", Smb1Action.NOOP, (4, 8)),
        ("a", Smb1Action.A, (4, 8, 12)),
        ("b", Smb1Action.B, (4, 8)),
        ("right", Smb1Action.RIGHT, (4, 8, 12)),
        ("right_a", Smb1Action.RIGHT_A, (4, 8, 12)),
        ("right_b", Smb1Action.RIGHT_B, (4, 8, 12)),
        ("right_a_b", Smb1Action.RIGHT_A_B, (4, 8, 12)),
        ("left", Smb1Action.LEFT, (4, 8)),
        ("left_a", Smb1Action.LEFT_A, (4, 8)),
        ("left_b", Smb1Action.LEFT_B, (4, 8)),
        ("left_a_b", Smb1Action.LEFT_A_B, (4, 8)),
    )
    out: list[PlanCandidate] = []
    for stem, action, durations in specs:
        for frames in durations:
            out.append(PlanCandidate(f"{stem}_{frames}", (ActionCommand(action, frames),)))
    return tuple(out)


PRECISION_CANDIDATES = _precision_candidates()

ACTION_LABELS = {
    "cruise": "RIGHT (30f)",
    "tap_jump": "RIGHT+A 6f -> RIGHT 24f",
    "medium_jump": "RIGHT+A 14f -> RIGHT 16f",
    "long_jump": "RIGHT+A 24f -> RIGHT 6f",
    "run": "RIGHT+B (30f)",
    "run_tap_jump": "RIGHT+A+B 6f -> RIGHT+B 24f",
    "run_medium_jump": "RIGHT+A+B 14f -> RIGHT+B 16f",
    "run_long_jump": "RIGHT+A+B 24f -> RIGHT+B 6f",
}
for candidate in PRECISION_CANDIDATES:
    ACTION_LABELS[candidate.name] = candidate.name.replace("_a_b_", "+A+B ").replace("_a_", "+A ").replace("_b_", "+B ").replace("_", " ") + "f"

v6.ACTION_LABELS.update(ACTION_LABELS)

FIXTURE_X = {
    "pre-collapse": 1091,
    "collapse-boundary": 1136,
    "doomed": 1266,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="B-aware adaptive SMB1 controller-sequence planner.")
    p.add_argument("rom", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    p.add_argument("--state-file", type=Path, default=Path("build/checkpoints/smb1-planner-v7.mss"))
    p.add_argument("--capture-dir", type=Path, default=Path("build/checkpoints/v7-cast-captures"))
    p.add_argument("--max-decisions", type=int, default=220)
    p.add_argument("--step-timeout", type=float, default=5.0)
    p.add_argument("--coarse-depth", type=int, default=5)
    p.add_argument("--coarse-width", type=int, default=10)
    p.add_argument("--precision-depth", type=int, default=3)
    p.add_argument("--precision-width", type=int, default=10)
    p.add_argument("--audit-interval", type=int, default=4)
    p.add_argument("--target-x", type=int, default=None)
    p.add_argument("--report-zip", type=Path, default=None, help="optional prior V5/V6 test-report ZIP; if omitted with --fixture, the newest local report is auto-discovered")
    p.add_argument("--fixture", choices=tuple(FIXTURE_X), default=None, help="fixture to replay from a prior report ZIP")
    return p.parse_args()


def action_label(name: str) -> str:
    return ACTION_LABELS.get(name, v6.action_label(name))


def load_state(core: MesenCore, path: Path):
    source = path.expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"save state not found or empty: {source}")
    set_nes_controller_state(core, 0, 0x00)
    before = core.frame_count()
    core.load_state_file(source)
    import time
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        state = read_smb1_state(core)
        obs = observation_from_state(core.frame_count(), state)
        if obs.native_frame_id != before and state.oper_mode == 1:
            return obs
        time.sleep(0.005)
    raise RuntimeError(f"save-state load did not become visible: {source}")


def _report_candidates() -> list[Path]:
    roots = [Path.cwd(), Path("build/test-reports")]
    patterns = ("fami-pixel-v6-test-report-*.zip", "fami-pixel-v5-test-report-*.zip")
    found: dict[Path, float] = {}
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                try:
                    found[path.resolve()] = path.stat().st_mtime
                except OSError:
                    pass
    return [p for p, _ in sorted(found.items(), key=lambda item: item[1], reverse=True)]


def resolve_report_zip(requested: Path | None) -> Path:
    if requested is not None:
        direct = requested.expanduser()
        try:
            resolved = direct.resolve()
        except OSError:
            resolved = direct.absolute()
        if resolved.is_file():
            return resolved

        # If the caller supplied a display/example path (for example D:\...\file.zip),
        # recover by basename before falling back to the newest report.
        basename = direct.name
        if basename and basename not in {".", ".."}:
            for candidate in _report_candidates():
                if candidate.name == basename:
                    print(f"Report ZIP : requested path missing; found by filename -> {candidate}", flush=True)
                    return candidate

    candidates = _report_candidates()
    if candidates:
        chosen = candidates[0]
        if requested is None:
            print(f"Report ZIP : auto-discovered newest prior report -> {chosen}", flush=True)
        else:
            print(f"Report ZIP : requested path missing; using newest prior report -> {chosen}", flush=True)
        return chosen

    if requested is None:
        raise FileNotFoundError(
            "no prior V5/V6 test-report ZIP found; expected one under build/test-reports or the current tree"
        )
    raise FileNotFoundError(f"report ZIP not found: {requested.expanduser()}")


def resolve_fixture(report_zip: Path, fixture: str) -> tuple[Path, Path]:
    archive = report_zip.expanduser().resolve()
    target_x = FIXTURE_X[fixture]
    temp_root = Path(tempfile.mkdtemp(prefix="fami-pixel-v7-fixture-"))
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(temp_root)
    matches = sorted(temp_root.rglob(f"*-authoritative-*-X-{target_x:04d}.mss"))
    if not matches:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise FileNotFoundError(f"fixture {fixture!r} (X={target_x}) not found in {archive}")
    return matches[0], temp_root


def finish_success(core: MesenCore, current, episode: EpisodeAccumulator, timeout_s: float) -> int:
    if current.game_engine_subroutine == FLAGPOLE_SLIDE:
        current = finish_flagpole(core, current, episode, timeout_s)
    result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
    print("\n=== LEVEL COMPLETE ===", flush=True)
    print(f"PlannerV7: PASS | frame={current.native_frame_id} X={current.mario_x_abs} maxX={result.max_x}", flush=True)
    return 0


def main() -> int:
    args = parse_args()
    if args.report_zip is not None and args.fixture is None:
        raise ValueError("--report-zip requires --fixture")

    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)
    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)
    cfg = configure_standard_nes_controller(core, port=1)
    print(f"NesConfig : PASS (sizeof={ctypes.sizeof(cfg)} Port1.Type={cfg.Port1.Type})", flush=True)
    if not core.load_rom(args.rom):
        print("LoadRom   : FAIL", flush=True)
        return 3
    print("LoadRom   : PASS", flush=True)
    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)

    fixture_temp: Path | None = None
    if args.fixture is not None:
        report_zip = resolve_report_zip(args.report_zip)
        fixture_path, fixture_temp = resolve_fixture(report_zip, args.fixture)
        current = load_state(core, fixture_path)
        print(f"Fixture   : {args.fixture} -> {fixture_path.name}", flush=True)
        print(f"Source ZIP: {report_zip}", flush=True)
        print(f"StateLoad : PASS | {v6.motion_summary(current)} | frame={current.native_frame_id}", flush=True)
    else:
        gameplay = enter_world_1_1(core, args.step_timeout)
        current = observation_from_state(core.frame_count(), gameplay)

    episode = EpisodeAccumulator(current)
    state_file = args.state_file.expanduser().resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    captures = v6.initialize_capture_dir(args.capture_dir)

    print("\n=== Planner V7: B-aware Controller Sequences ===", flush=True)
    print(f"Coarse actions  : {len(COARSE_CANDIDATES)} including RIGHT+B and RIGHT+A+B run/jump macros", flush=True)
    print(f"Precision actions: {len(PRECISION_CANDIDATES)} short A/B/horizontal combinations", flush=True)
    print("Control model   : horizontal intent x A state x B state x explicit duration", flush=True)
    print(f"Precision beam  : depth={args.precision_depth} width={args.precision_width}", flush=True)

    try:
        for decision in range(1, args.max_decisions + 1):
            current = observation_from_state(core.frame_count(), read_smb1_state(core))
            if current.game_engine_subroutine == FLAGPOLE_SLIDE:
                return finish_success(core, current, episode, args.step_timeout)
            if args.target_x is not None and current.mario_x_abs >= args.target_x:
                print(f"PlannerV7: PASS target reached | X={current.mario_x_abs} target={args.target_x}", flush=True)
                return 0

            frame, x, engine = save_checkpoint(core, state_file)
            v6.capture_authoritative(state_file, captures, decision, frame, x)
            coarse = v6.evaluate_candidates(
                core, COARSE_CANDIDATES, state_file, frame, x, engine, current,
                args.step_timeout, capture_dir=captures, decision=decision, mode="coarse",
            )
            trigger = v6.precision_trigger(coarse)

            print("\n------------------------------------------------------------", flush=True)
            print(f"Decision #{decision:03d} | frame {frame} | Mario X={x}", flush=True)
            print("Coarse choices:", flush=True)
            for e in coarse:
                print(f"  {action_label(e.outcome.candidate.name):46s} -> {v6.outcome_summary(e)}", flush=True)

            if trigger is not None:
                print(f"CONTROL ALERT : {trigger} -> B-AWARE PRECISION MODE", flush=True)
                precision = v6.evaluate_candidates(
                    core, PRECISION_CANDIDATES, state_file, frame, x, engine, current,
                    args.step_timeout, capture_dir=captures, decision=decision, mode="precision",
                )
                print("Precision choices:", flush=True)
                for e in precision:
                    print(f"  {action_label(e.outcome.candidate.name):46s} -> {v6.outcome_summary(e)}", flush=True)
                root_name, logs = v6.beam_search(
                    core, PRECISION_CANDIDATES, state_file, frame, x, engine, current,
                    args.step_timeout, args.precision_depth, args.precision_width, "v7-precision",
                )
                for line in logs:
                    print(line, flush=True)
                chosen = next(e for e in precision if e.outcome.candidate.name == root_name)
                mode = "B-AWARE PRECISION"
            elif decision % args.audit_interval == 0:
                print("Safety audit  : scheduled B-aware coarse lookahead", flush=True)
                root_name, logs = v6.beam_search(
                    core, COARSE_CANDIDATES, state_file, frame, x, engine, current,
                    args.step_timeout, args.coarse_depth, args.coarse_width, "v7-coarse",
                )
                for line in logs:
                    print(line, flush=True)
                chosen = next(e for e in coarse if e.outcome.candidate.name == root_name)
                mode = "B-AWARE COARSE BEAM"
            else:
                chosen = v6.select_immediate(coarse)
                mode = "B-AWARE COARSE FAST"

            print(f"Selected action: {action_label(chosen.outcome.candidate.name)}", flush=True)
            print(f"Decision mode  : {mode}", flush=True)
            restore_checkpoint(core, state_file, frame, x, engine)
            current, terminal, flag = commit_candidate(
                core, chosen.outcome.candidate, current, episode, args.step_timeout
            )
            print(f"Committed state: frame={current.native_frame_id} | {v6.motion_summary(current)}", flush=True)

            if terminal == CandidateTerminal.DEATH:
                result = episode.finish(EpisodeTermination.DEATH)
                print(f"PlannerV7: FAIL death | frame={current.native_frame_id} X={current.mario_x_abs} maxX={result.max_x}", flush=True)
                return 6
            if terminal == CandidateTerminal.LEVEL_COMPLETE or flag:
                return finish_success(core, current, episode, args.step_timeout)

        result = episode.finish(EpisodeTermination.TIMEOUT)
        print(f"PlannerV7: FAIL decision limit | X={current.mario_x_abs} maxX={result.max_x}", flush=True)
        return 7
    finally:
        if fixture_temp is not None:
            shutil.rmtree(fixture_temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
