#!/usr/bin/env python3
"""Adaptive control-space checkpoint planner for SMB1 World 1-1.

V6 responds to the V5 binary evidence: equal X progress can hide materially
different machine states, and one decision later the existing four 30-frame
macros can collapse to the same full machine state. V6 therefore keeps the V5
state-diverse beam idea but adds a precision control mode before controllability
collapses.

Normal mode uses the validated 30-frame macros. Precision mode is triggered by
no progress, candidate-state convergence, or a progress tie whose endpoint
motion states differ. Precision mode uses short NOOP/LEFT/A/LEFT+A/RIGHT/RIGHT+A
primitives and a bounded state-diverse beam. Mesen remains machine authority;
only the first selected root action is committed.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fami_pixel.adapters.mesen import MesenCore, configure_standard_nes_controller
from fami_pixel.games.smb1 import (
    ActionCommand,
    CandidateOutcome,
    CandidateTerminal,
    EpisodeAccumulator,
    EpisodeTermination,
    PlanCandidate,
    Smb1Action,
    observation_from_state,
    read_smb1_state,
    score_candidate,
)
from mesen_smb_checkpoint_planner import (
    CANDIDATES as COARSE_CANDIDATES,
    FAIL_MARKER,
    FLAGPOLE_SLIDE,
    READY_MARKER,
    commit_candidate,
    enter_world_1_1,
    finish_flagpole,
    restore_checkpoint,
    run_candidate,
    save_checkpoint,
)


PRECISION_CANDIDATES = (
    PlanCandidate("noop_6", (ActionCommand(Smb1Action.NOOP, 6),)),
    PlanCandidate("noop_12", (ActionCommand(Smb1Action.NOOP, 12),)),
    PlanCandidate("left_6", (ActionCommand(Smb1Action.LEFT, 6),)),
    PlanCandidate("left_12", (ActionCommand(Smb1Action.LEFT, 12),)),
    PlanCandidate("a_6", (ActionCommand(Smb1Action.A, 6),)),
    PlanCandidate("a_12", (ActionCommand(Smb1Action.A, 12),)),
    PlanCandidate("left_a_6", (ActionCommand(Smb1Action.LEFT_A, 6),)),
    PlanCandidate("left_a_12", (ActionCommand(Smb1Action.LEFT_A, 12),)),
    PlanCandidate("right_8", (ActionCommand(Smb1Action.RIGHT, 8),)),
    PlanCandidate("right_a_4", (ActionCommand(Smb1Action.RIGHT_A, 4),)),
    PlanCandidate("right_a_8", (ActionCommand(Smb1Action.RIGHT_A, 8),)),
    PlanCandidate("right_a_12", (ActionCommand(Smb1Action.RIGHT_A, 12),)),
)

ACTION_LABELS = {
    "cruise": "RIGHT only (30f)",
    "tap_jump": "short jump (RIGHT+A 6f, RIGHT 24f)",
    "medium_jump": "medium jump (RIGHT+A 14f, RIGHT 16f)",
    "long_jump": "long jump (RIGHT+A 24f, RIGHT 6f)",
    "noop_6": "wait / release controls (6f)",
    "noop_12": "wait / release controls (12f)",
    "left_6": "step LEFT (6f)",
    "left_12": "step LEFT (12f)",
    "a_6": "A only (6f)",
    "a_12": "A only (12f)",
    "left_a_6": "LEFT+A (6f)",
    "left_a_12": "LEFT+A (12f)",
    "right_8": "RIGHT (8f)",
    "right_a_4": "RIGHT+A (4f)",
    "right_a_8": "RIGHT+A (8f)",
    "right_a_12": "RIGHT+A (12f)",
}
CAPTURE_INDEX_HEADER = "kind\tdecision\tmode\taction\tframe\tmario_x\tsize_bytes\tsha256\tcheckpoint\n"


@dataclass(frozen=True)
class EvaluatedCandidate:
    outcome: CandidateOutcome
    observation: object

    @property
    def signature(self) -> tuple[int, ...]:
        o = self.observation
        return (
            o.mario_x_abs,
            o.mario_y,
            o.mario_y_high,
            o.player_state,
            o.player_x_speed,
            o.player_y_speed,
            o.game_engine_subroutine,
        )


@dataclass(frozen=True)
class BeamNode:
    root_name: str
    checkpoint_path: Path
    observation: object
    start_x: int
    max_x: int
    elapsed_frames: int
    terminal: CandidateTerminal
    reached_flagpole: bool
    trace: tuple[str, ...]

    @property
    def progress(self) -> int:
        return self.observation.mario_x_abs - self.start_x

    @property
    def signature(self) -> tuple[int, ...]:
        o = self.observation
        return (
            o.mario_x_abs,
            o.mario_y,
            o.mario_y_high,
            o.player_state,
            o.player_x_speed,
            o.player_y_speed,
            o.game_engine_subroutine,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Adaptive control-space SMB1 checkpoint planner.")
    p.add_argument("rom", type=Path)
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home"))
    p.add_argument("--state-file", type=Path, default=Path("build/checkpoints/smb1-planner-v6.mss"))
    p.add_argument("--capture-dir", type=Path, default=Path("build/checkpoints/v6-cast-captures"))
    p.add_argument("--max-decisions", type=int, default=220)
    p.add_argument("--step-timeout", type=float, default=5.0)
    p.add_argument("--coarse-depth", type=int, default=5)
    p.add_argument("--coarse-width", type=int, default=8)
    p.add_argument("--precision-depth", type=int, default=4)
    p.add_argument("--precision-width", type=int, default=8)
    p.add_argument("--audit-interval", type=int, default=4)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def fail(message: str) -> None:
    print(message, flush=True)
    print(FAIL_MARKER, flush=True)
    threading.Event().wait()


def signed_byte(v: int) -> int:
    return v - 256 if v >= 128 else v


def action_label(name: str) -> str:
    return ACTION_LABELS.get(name, name)


def terminal_label(t: CandidateTerminal) -> str:
    if t == CandidateTerminal.DEATH:
        return "DEATH"
    if t == CandidateTerminal.LEVEL_COMPLETE:
        return "LEVEL COMPLETE"
    return "SAFE"


def motion_summary(o) -> str:
    return (
        f"X={o.mario_x_abs}, Y={o.mario_y}, player_state={o.player_state}, "
        f"horizontal_speed={signed_byte(o.player_x_speed):+d}, "
        f"vertical_speed={signed_byte(o.player_y_speed):+d}, "
        f"engine=0x{o.game_engine_subroutine:02X}"
    )


def outcome_summary(e: EvaluatedCandidate) -> str:
    o = e.outcome
    return (
        f"progress={o.progress:+d}, score={score_candidate(o):.3f}, "
        f"status={terminal_label(o.terminal)}, state=({motion_summary(e.observation)})"
    )


def initialize_capture_dir(path: Path) -> Path:
    d = path.expanduser().resolve()
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.tsv").write_text(CAPTURE_INDEX_HEADER, encoding="utf-8")
    return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def record_capture(d: Path, path: Path, *, kind: str, decision: int, mode: str, action: str, frame: int, mario_x: int) -> None:
    digest = sha256_file(path)
    with (d / "index.tsv").open("a", encoding="utf-8", newline="") as f:
        f.write(f"{kind}\t{decision}\t{mode}\t{action}\t{frame}\t{mario_x}\t{path.stat().st_size}\t{digest}\t{path.name}\n")


def capture_authoritative(state_file: Path, d: Path, decision: int, frame: int, x: int) -> None:
    p = d / f"decision-{decision:03d}-authoritative-frame-{frame:06d}-X-{x:04d}.mss"
    shutil.copy2(state_file, p)
    record_capture(d, p, kind="authoritative", decision=decision, mode="root", action="-", frame=frame, mario_x=x)
    print(f"Binary capture : authoritative decision {decision:03d} sha256={sha256_file(p)[:12]}...", flush=True)


def capture_endpoint(core: MesenCore, d: Path, decision: int, mode: str, candidate: PlanCandidate) -> None:
    s = read_smb1_state(core)
    o = observation_from_state(core.frame_count(), s)
    p = d / f"decision-{decision:03d}-{mode}-{candidate.name}-frame-{o.native_frame_id:06d}-X-{o.mario_x_abs:04d}.mss"
    frame, x, _ = save_checkpoint(core, p)
    record_capture(d, p, kind="root-candidate", decision=decision, mode=mode, action=candidate.name, frame=frame, mario_x=x)


def evaluate_candidates(core: MesenCore, candidates: tuple[PlanCandidate, ...], checkpoint_path: Path, checkpoint_frame: int, checkpoint_x: int, checkpoint_engine: int, start_observation, timeout_s: float, *, capture_dir: Path | None = None, decision: int | None = None, mode: str = "coarse") -> tuple[EvaluatedCandidate, ...]:
    results: list[EvaluatedCandidate] = []
    for candidate in candidates:
        restore_checkpoint(core, checkpoint_path, checkpoint_frame, checkpoint_x, checkpoint_engine)
        outcome = run_candidate(core, candidate, start_observation, timeout_s)
        state = read_smb1_state(core)
        observation = observation_from_state(core.frame_count(), state)
        results.append(EvaluatedCandidate(outcome, observation))
        if capture_dir is not None and decision is not None:
            capture_endpoint(core, capture_dir, decision, mode, candidate)
    return tuple(results)


def select_immediate(results: tuple[EvaluatedCandidate, ...]) -> EvaluatedCandidate:
    return max(results, key=lambda e: (score_candidate(e.outcome), e.outcome.progress, e.outcome.max_x))


def precision_trigger(results: tuple[EvaluatedCandidate, ...]) -> str | None:
    safe = [e for e in results if e.outcome.terminal == CandidateTerminal.NONE]
    if not safe:
        return "all-coarse-actions-terminal"
    best_progress = max(e.outcome.progress for e in safe)
    if best_progress <= 0:
        return "no-forward-progress"
    top = [e for e in safe if e.outcome.progress == best_progress]
    if len(top) >= 2 and len({e.signature for e in top}) > 1:
        return "state-divergent-progress-tie"
    if len({e.signature for e in safe}) == 1 and len(safe) > 1:
        return "action-convergence"
    return None


def node_score(n: BeamNode) -> float:
    if n.terminal == CandidateTerminal.LEVEL_COMPLETE:
        return 1_000_000.0
    if n.terminal == CandidateTerminal.DEATH:
        return -1_000_000.0
    if n.reached_flagpole:
        return 100_000.0 + n.progress
    return n.progress / max(1, n.elapsed_frames)


def select_diverse(nodes: list[BeamNode], width: int) -> list[BeamNode]:
    ordered = sorted(nodes, key=lambda n: (node_score(n), n.progress, n.max_x), reverse=True)
    out: list[BeamNode] = []
    seen: set[tuple[int, ...]] = set()
    for n in ordered:
        if n.signature in seen:
            continue
        seen.add(n.signature)
        out.append(n)
        if len(out) >= width:
            break
    return out


def beam_search(core: MesenCore, candidates: tuple[PlanCandidate, ...], root_file: Path, root_frame: int, root_x: int, root_engine: int, root_observation, timeout_s: float, depth: int, width: int, tag: str) -> tuple[str, tuple[str, ...]]:
    work = root_file.parent / f"v6-{tag}-beam"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    counter = 0
    frontier: list[BeamNode] = []
    order = {c.name: i for i, c in enumerate(candidates)}

    for c in candidates:
        restore_checkpoint(core, root_file, root_frame, root_x, root_engine)
        outcome = run_candidate(core, c, root_observation, timeout_s)
        obs = observation_from_state(core.frame_count(), read_smb1_state(core))
        counter += 1
        p = work / f"d01-n{counter:04d}.mss"
        if outcome.terminal == CandidateTerminal.NONE and not outcome.reached_flagpole:
            save_checkpoint(core, p)
        frontier.append(BeamNode(c.name, p, obs, root_x, outcome.max_x, outcome.elapsed_frames, outcome.terminal, outcome.reached_flagpole, (c.name,)))

    frontier = select_diverse(frontier, max(width, len(candidates)))
    logs = [f"  {tag.upper()} beam depth 1: {len(frontier)} distinct states"]

    for d in range(2, depth + 1):
        if not frontier or any(n.terminal == CandidateTerminal.LEVEL_COMPLETE or n.reached_flagpole for n in frontier):
            break
        children: list[BeamNode] = []
        for n in frontier:
            if n.terminal != CandidateTerminal.NONE or n.reached_flagpole:
                children.append(n)
                continue
            obs = n.observation
            for c in candidates:
                restore_checkpoint(core, n.checkpoint_path, obs.native_frame_id, obs.mario_x_abs, obs.game_engine_subroutine)
                outcome = run_candidate(core, c, obs, timeout_s)
                child_obs = observation_from_state(core.frame_count(), read_smb1_state(core))
                counter += 1
                p = work / f"d{d:02d}-n{counter:04d}.mss"
                if outcome.terminal == CandidateTerminal.NONE and not outcome.reached_flagpole:
                    save_checkpoint(core, p)
                children.append(BeamNode(n.root_name, p, child_obs, n.start_x, max(n.max_x, outcome.max_x), n.elapsed_frames + outcome.elapsed_frames, outcome.terminal, n.reached_flagpole or outcome.reached_flagpole, n.trace + (c.name,)))
        frontier = select_diverse(children, width)
        logs.append(f"  {tag.upper()} beam depth {d}: {len(frontier)} distinct states")

    best = max(frontier, key=lambda n: (node_score(n), n.progress, n.max_x, -order[n.root_name]))
    logs.append(f"  Beam choice: {action_label(best.root_name)} | projected progress={best.progress:+d} | status={terminal_label(best.terminal)}")
    logs.append("  Projected path: " + " -> ".join(action_label(x) for x in best.trace))
    return best.root_name, tuple(logs)


def finish_success(core: MesenCore, current, episode: EpisodeAccumulator, timeout_s: float) -> None:
    if current.game_engine_subroutine == FLAGPOLE_SLIDE:
        current = finish_flagpole(core, current, episode, timeout_s)
    result = episode.finish(EpisodeTermination.LEVEL_COMPLETE)
    print("\n=== LEVEL COMPLETE ===", flush=True)
    print(f"Mario completed World 1-1 at frame {current.native_frame_id}, X={current.mario_x_abs}.", flush=True)
    print(f"Episode: LEVEL COMPLETE | frames={result.elapsed_frames} | max X={result.max_x} | progress={result.net_progress:+d}", flush=True)
    print("PlannerV6: PASS - adaptive control-space planning completed World 1-1", flush=True)
    print(READY_MARKER, flush=True)
    threading.Event().wait()


def worker(args: argparse.Namespace) -> None:
    core = MesenCore(args.dll)
    print(f"DLL       : {core.path}", flush=True)
    print(f"ROM       : {args.rom.expanduser().resolve()}", flush=True)
    core.initialize_headless(args.home)
    print("Init      : PASS", flush=True)
    cfg = configure_standard_nes_controller(core, port=1)
    print(f"NesConfig : PASS (sizeof={ctypes.sizeof(cfg)} Port1.Type={cfg.Port1.Type})", flush=True)
    if not core.load_rom(args.rom):
        fail("LoadRom   : FAIL")
    print("LoadRom   : PASS", flush=True)
    core.initialize_debugger()
    print("Debugger  : PASS", flush=True)

    gameplay = enter_world_1_1(core, args.step_timeout)
    current = observation_from_state(core.frame_count(), gameplay)
    episode = EpisodeAccumulator(current)
    state_file = args.state_file.expanduser().resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    captures = initialize_capture_dir(args.capture_dir)

    print("\n=== Planner V6: Adaptive Control Space ===", flush=True)
    print("Normal mode    : validated 30-frame coarse macros", flush=True)
    print("Precision mode : 4-12 frame NOOP/LEFT/A/LEFT+A/RIGHT/RIGHT+A primitives", flush=True)
    print("Precision trigger: no-progress | state-divergent progress tie | action convergence", flush=True)
    print(f"Coarse beam    : depth={args.coarse_depth} width={args.coarse_width}", flush=True)
    print(f"Precision beam : depth={args.precision_depth} width={args.precision_width}", flush=True)
    print(f"Binary capture : worker-owned -> {captures}", flush=True)

    for decision in range(1, args.max_decisions + 1):
        current = observation_from_state(core.frame_count(), read_smb1_state(core))
        if current.game_engine_subroutine == FLAGPOLE_SLIDE:
            finish_success(core, current, episode, args.step_timeout)

        frame, x, engine = save_checkpoint(core, state_file)
        capture_authoritative(state_file, captures, decision, frame, x)
        coarse = evaluate_candidates(core, COARSE_CANDIDATES, state_file, frame, x, engine, current, args.step_timeout, capture_dir=captures, decision=decision, mode="coarse")
        trigger = precision_trigger(coarse)

        print("\n------------------------------------------------------------", flush=True)
        print(f"Decision #{decision:03d} | frame {frame} | Mario X={x}", flush=True)
        print("Coarse choices:", flush=True)
        for e in coarse:
            print(f"  {action_label(e.outcome.candidate.name):42s} -> {outcome_summary(e)}", flush=True)

        chosen: EvaluatedCandidate
        mode: str
        if trigger is not None:
            print(f"CONTROL ALERT : {trigger} -> switching to PRECISION MODE", flush=True)
            precision = evaluate_candidates(core, PRECISION_CANDIDATES, state_file, frame, x, engine, current, args.step_timeout, capture_dir=captures, decision=decision, mode="precision")
            print("Precision choices:", flush=True)
            for e in precision:
                print(f"  {action_label(e.outcome.candidate.name):42s} -> {outcome_summary(e)}", flush=True)
            root_name, logs = beam_search(core, PRECISION_CANDIDATES, state_file, frame, x, engine, current, args.step_timeout, args.precision_depth, args.precision_width, "precision")
            for line in logs:
                print(line, flush=True)
            chosen = next(e for e in precision if e.outcome.candidate.name == root_name)
            mode = "PRECISION"
        elif decision % args.audit_interval == 0:
            print("Safety audit  : scheduled coarse state-diverse lookahead", flush=True)
            root_name, logs = beam_search(core, COARSE_CANDIDATES, state_file, frame, x, engine, current, args.step_timeout, args.coarse_depth, args.coarse_width, "coarse")
            for line in logs:
                print(line, flush=True)
            chosen = next(e for e in coarse if e.outcome.candidate.name == root_name)
            mode = "COARSE BEAM"
        else:
            chosen = select_immediate(coarse)
            mode = "COARSE FAST"

        print(f"Selected action: {action_label(chosen.outcome.candidate.name)}", flush=True)
        print(f"Decision mode  : {mode}", flush=True)
        print(f"Expected result: {outcome_summary(chosen)}", flush=True)

        restore_checkpoint(core, state_file, frame, x, engine)
        current, terminal, flag = commit_candidate(core, chosen.outcome.candidate, current, episode, args.step_timeout)
        print(f"Committed state: frame={current.native_frame_id} | {motion_summary(current)}", flush=True)

        if terminal == CandidateTerminal.DEATH:
            result = episode.finish(EpisodeTermination.DEATH)
            fail(f"PlannerV6: FAIL - selected action led to DEATH | frame={current.native_frame_id} | X={current.mario_x_abs} | max X={result.max_x}")
        if terminal == CandidateTerminal.LEVEL_COMPLETE or flag:
            finish_success(core, current, episode, args.step_timeout)

    result = episode.finish(EpisodeTermination.TIMEOUT)
    fail(f"PlannerV6: FAIL - decision limit reached | limit={args.max_decisions} | frame={current.native_frame_id} | X={current.mario_x_abs} | max X={result.max_x}")


def supervisor(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(Path(__file__).resolve()), str(args.rom), "--dll", str(args.dll), "--home", str(args.home), "--state-file", str(args.state_file), "--capture-dir", str(args.capture_dir), "--max-decisions", str(args.max_decisions), "--step-timeout", str(args.step_timeout), "--coarse-depth", str(args.coarse_depth), "--coarse-width", str(args.coarse_width), "--precision-depth", str(args.precision_depth), "--precision-width", str(args.precision_width), "--audit-interval", str(args.audit_interval), "--timeout", str(args.timeout), "--worker"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    q: queue.Queue[str | None] = queue.Queue()
    assert p.stdout is not None

    def reader() -> None:
        try:
            for line in p.stdout:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    ready = failed = closed = False
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            try:
                item = q.get(timeout=0.1)
            except queue.Empty:
                if p.poll() is not None and closed:
                    break
                continue
            if item is None:
                closed = True
                if p.poll() is not None:
                    break
                continue
            print(item, end="")
            if READY_MARKER in item:
                ready = True
                break
            if FAIL_MARKER in item:
                failed = True
                break
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=2.0)
    if ready:
        print("WorkerExit: FORCED (expected containment)")
        print("Supervisor: PASS")
        return 0
    if failed:
        print("WorkerExit: FORCED (failed probe contained)")
        print("Supervisor: FAIL")
        return 6
    code = p.returncode
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
