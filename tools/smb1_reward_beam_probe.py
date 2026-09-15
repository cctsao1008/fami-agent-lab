#!/usr/bin/env python3
"""Search multi-chunk SMB1 reward interceptions from one saved Mesen scenario.

Unlike ``smb1_trajectory_probe.py``, which deliberately evaluates one fixed macro
plus a fixed tail, this tool performs a bounded receding-style beam search over
4-frame action chunks. It exists to answer the next issue #32 question from the
V24 Star field evidence: can a *sequence* such as brake -> wait -> backtrack
collect the already-visible target while surviving the nearby enemy?

Mesen remains authoritative. A Star succeeds only when the native invincibility
timer increases; object disappearance alone is never accepted as collection.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from fami_pixel.adapters.mesen import (
    MesenCore,
    configure_standard_nes_controller,
    set_nes_controller_state,
)
from fami_pixel.games.smb1 import (
    GameEventType,
    derive_game_events,
    observation_from_state,
    read_smb1_state,
)
from fami_pixel.games.smb1.radar import read_smb1_radar
from fami_pixel.games.smb1.reward_beam import (
    REWARD_BEAM_CHUNKS,
    RewardBeamChunk,
    buttons_for_chunk_frame,
    matching_reward,
    reward_beam_key,
    reward_collection_proven,
)


_DONE = "RewardBeamProbe: DONE"
_GRACE_S = 0.75


@dataclass(frozen=True)
class BeamNode:
    state_file: Path
    path: tuple[str, ...]
    frame: int
    mario_x: int
    mario_y: int
    key: tuple[int, int, int, int, int]
    target_dx: int | None
    target_state: int | None
    nearest_enemy_dx: int | None


@dataclass(frozen=True)
class ChunkOutcome:
    died: bool
    won: bool
    collected: bool
    frame: int
    mario_x: int
    mario_y: int
    radar: dict


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bounded exact-Mesen beam search for SMB1 reward interception")
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--target-reward", choices=("mushroom", "fire_flower", "star", "one_up"))
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-reward-beam-probe"))
    p.add_argument("--depth", type=int, default=8, help="maximum 4-frame chunk depth (default: 8)")
    p.add_argument("--beam-width", type=int, default=16, help="alive states retained per depth (default: 16)")
    p.add_argument("--step-timeout", type=float, default=2.0)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    if args.depth <= 0:
        p.error("--depth must be > 0")
    if args.beam_width <= 0:
        p.error("--beam-width must be > 0")
    return args


def _load_manifest(scenario_dir: Path) -> tuple[dict, Path]:
    manifest_path = scenario_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"scenario manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid scenario manifest: {manifest_path}: {exc}") from exc
    state_file = scenario_dir / str(manifest.get("state_file", "root.mss"))
    if not state_file.is_file():
        raise SystemExit(f"scenario state not found: {state_file}")
    return manifest, state_file


def _restore(core: MesenCore, state_file: Path, *, expected_frame: int, expected_x: int) -> None:
    core.load_state_file(state_file)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        state = read_smb1_state(core)
        if core.frame_count() == int(expected_frame) and state.player_absolute_x == int(expected_x):
            return
        time.sleep(0.002)
    state = read_smb1_state(core)
    raise RuntimeError(
        "beam state restore did not converge: "
        f"expected frame={expected_frame} x={expected_x}, "
        f"actual frame={core.frame_count()} x={state.player_absolute_x}"
    )


def _radar(core: MesenCore, mario_x: int) -> dict:
    return read_smb1_radar(core, player_x=mario_x).to_payload()


def _simulate_chunk(
    core: MesenCore,
    chunk: RewardBeamChunk,
    *,
    target_reward: str,
    baseline_player_status: int,
    baseline_star_timer: int,
    step_timeout: float,
) -> ChunkOutcome:
    previous = observation_from_state(core.frame_count(), read_smb1_state(core))
    current = previous
    radar = _radar(core, current.mario_x_abs)

    for offset in range(chunk.frame_count):
        set_nes_controller_state(core, 0, buttons_for_chunk_frame(chunk, offset))
        core.step_frame_sync(1, max(1, int(step_timeout * 1000.0)))
        current = observation_from_state(core.frame_count(), read_smb1_state(core))
        radar = _radar(core, current.mario_x_abs)
        events = derive_game_events(previous, current)
        died = any(event.kind == GameEventType.DIED for event in events)
        won = any(event.kind == GameEventType.LEVEL_COMPLETED for event in events)
        collected = reward_collection_proven(
            target_reward,
            baseline_player_status=baseline_player_status,
            baseline_star_timer=baseline_star_timer,
            radar=radar,
        )
        if died or won or collected:
            try:
                set_nes_controller_state(core, 0, 0x00)
            except Exception:
                pass
            return ChunkOutcome(
                died=died,
                won=won,
                collected=collected,
                frame=int(current.native_frame_id),
                mario_x=int(current.mario_x_abs),
                mario_y=int(current.mario_y),
                radar=radar,
            )
        previous = current

    try:
        set_nes_controller_state(core, 0, 0x00)
    except Exception:
        pass
    return ChunkOutcome(
        died=False,
        won=False,
        collected=False,
        frame=int(current.native_frame_id),
        mario_x=int(current.mario_x_abs),
        mario_y=int(current.mario_y),
        radar=radar,
    )


def _node_from_outcome(state_file: Path, path: tuple[str, ...], outcome: ChunkOutcome, target_reward: str) -> BeamNode:
    reward = matching_reward(outcome.radar, target_reward)
    enemy = outcome.radar.get("nearest_enemy_dx")
    enemy_dx = None if enemy is None else int(enemy)
    key = reward_beam_key(
        reward=reward,
        nearest_enemy_dx=enemy_dx,
        mario_x=outcome.mario_x,
    )
    return BeamNode(
        state_file=state_file,
        path=path,
        frame=outcome.frame,
        mario_x=outcome.mario_x,
        mario_y=outcome.mario_y,
        key=key,
        target_dx=None if reward is None else int(reward["dx"]),
        target_state=None if reward is None else int(reward.get("state", 0)),
        nearest_enemy_dx=enemy_dx,
    )


def _path_text(path: tuple[str, ...]) -> str:
    return " -> ".join(path) if path else "ROOT"


def worker(args: argparse.Namespace) -> int:
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest, root_state = _load_manifest(scenario_dir)
    target_reward = args.target_reward or manifest.get("selection_reward_type")
    if target_reward not in {"mushroom", "fire_flower", "star", "one_up"}:
        raise SystemExit("--target-reward is required when the scenario manifest has no reward type")

    work_dir = Path("build/beam-probes") / str(manifest.get("id") or scenario_dir.name)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()

    root_frame = int(manifest.get("native_frame"))
    root_x = int(manifest.get("mario_x"))
    _restore(core, root_state, expected_frame=root_frame, expected_x=root_x)
    root_obs = observation_from_state(core.frame_count(), read_smb1_state(core))
    root_radar = _radar(core, root_obs.mario_x_abs)
    root_reward = matching_reward(root_radar, str(target_reward))
    if root_reward is None:
        raise SystemExit(
            f"target reward {target_reward!r} is not visible at the scenario root; "
            "use a reward-visible checkpoint for interception search"
        )

    baseline_status = int(root_radar.get("player_status", 0))
    baseline_star_timer = int(root_radar.get("star_invincible_timer", 0))
    root_enemy = root_radar.get("nearest_enemy_dx")
    root_key = reward_beam_key(
        reward=root_reward,
        nearest_enemy_dx=None if root_enemy is None else int(root_enemy),
        mario_x=int(root_obs.mario_x_abs),
    )
    root = BeamNode(
        state_file=root_state,
        path=(),
        frame=int(root_obs.native_frame_id),
        mario_x=int(root_obs.mario_x_abs),
        mario_y=int(root_obs.mario_y),
        key=root_key,
        target_dx=int(root_reward["dx"]),
        target_state=int(root_reward.get("state", 0)),
        nearest_enemy_dx=None if root_enemy is None else int(root_enemy),
    )

    print(f"Scenario   : {manifest.get('id')}", flush=True)
    print(
        f"Root       : frame={root.frame} X={root.mario_x} Y={root.mario_y} "
        f"target={target_reward} dx={root.target_dx} state={root.target_state} "
        f"enemy_dx={root.nearest_enemy_dx}",
        flush=True,
    )
    print(
        f"Search     : depth={args.depth} chunks x 4f, beam={args.beam_width}, "
        f"vocab={len(REWARD_BEAM_CHUNKS)}",
        flush=True,
    )
    print("Objective  : authoritative collection > keep target visible/close > enemy clearance", flush=True)

    beam = [root]
    expansions = 0
    deaths = 0
    target_lost = 0

    try:
        for depth in range(1, args.depth + 1):
            children: list[BeamNode] = []
            depth_deaths = 0
            depth_lost = 0

            for parent_index, parent in enumerate(beam):
                for chunk_index, chunk in enumerate(REWARD_BEAM_CHUNKS):
                    expansions += 1
                    _restore(
                        core,
                        parent.state_file,
                        expected_frame=parent.frame,
                        expected_x=parent.mario_x,
                    )
                    outcome = _simulate_chunk(
                        core,
                        chunk,
                        target_reward=str(target_reward),
                        baseline_player_status=baseline_status,
                        baseline_star_timer=baseline_star_timer,
                        step_timeout=float(args.step_timeout),
                    )
                    path = parent.path + (chunk.name,)

                    if outcome.collected:
                        timer = int(outcome.radar.get("star_invincible_timer", 0))
                        status = int(outcome.radar.get("player_status", 0))
                        print(
                            f"COLLECTED  : depth={depth} frame={outcome.frame} X={outcome.mario_x} "
                            f"Y={outcome.mario_y} path={_path_text(path)}",
                            flush=True,
                        )
                        print(
                            f"PROOF      : player_status={baseline_status}->{status} "
                            f"star_timer={baseline_star_timer}->{timer}",
                            flush=True,
                        )
                        print(f"EXPANSIONS : {expansions} death={deaths + depth_deaths} target_lost={target_lost + depth_lost}", flush=True)
                        print(_DONE, flush=True)
                        return 0

                    if outcome.died or outcome.won:
                        if outcome.died:
                            depth_deaths += 1
                        continue

                    child_file = work_dir / f"d{depth:02d}-p{parent_index:03d}-c{chunk_index:02d}.mss"
                    core.save_state_file(child_file)
                    child = _node_from_outcome(child_file, path, outcome, str(target_reward))
                    if child.target_dx is None:
                        depth_lost += 1
                    children.append(child)

            deaths += depth_deaths
            target_lost += depth_lost
            if not children:
                print(
                    f"DEPTH {depth:02d}  : no alive children | deaths={depth_deaths}",
                    flush=True,
                )
                print("NO COLLECT : beam exhausted before authoritative collection", flush=True)
                print(_DONE, flush=True)
                return 2

            children.sort(key=lambda node: node.key, reverse=True)
            keep = children[: args.beam_width]
            keep_paths = {node.state_file for node in keep}
            for node in children[args.beam_width :]:
                try:
                    node.state_file.unlink()
                except OSError:
                    pass

            best = keep[0]
            visible_count = sum(node.target_dx is not None for node in keep)
            print(
                f"DEPTH {depth:02d}  : alive={len(children):3d} keep={len(keep):2d} "
                f"visible={visible_count:2d} deaths={depth_deaths:2d} lost={depth_lost:2d} | "
                f"best dx={best.target_dx} state={best.target_state} enemy={best.nearest_enemy_dx} "
                f"X={best.mario_x} Y={best.mario_y} path={_path_text(best.path)}",
                flush=True,
            )

            # Parent states in the temporary work directory are no longer part
            # of the frontier after expansion. The scenario root lives elsewhere.
            for parent in beam:
                if parent.state_file.parent == work_dir and parent.state_file not in keep_paths:
                    try:
                        parent.state_file.unlink()
                    except OSError:
                        pass
            beam = keep

        best = max(beam, key=lambda node: node.key)
        print(
            f"NO COLLECT : reached depth cap without authoritative {target_reward} collection",
            flush=True,
        )
        print(
            f"BEST       : dx={best.target_dx} state={best.target_state} enemy={best.nearest_enemy_dx} "
            f"X={best.mario_x} path={_path_text(best.path)}",
            flush=True,
        )
        print(f"EXPANSIONS : {expansions} death={deaths} target_lost={target_lost}", flush=True)
        print(_DONE, flush=True)
        return 1
    finally:
        try:
            set_nes_controller_state(core, 0, 0x00)
            core.stop()
            core.release()
        except Exception:
            pass


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()


def supervise(args: argparse.Namespace) -> int:
    _load_manifest(args.scenario_dir.expanduser().resolve())
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    saw_done = False
    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            if _DONE in line:
                saw_done = True
                try:
                    return proc.wait(timeout=_GRACE_S)
                except subprocess.TimeoutExpired:
                    _terminate_tree(proc)
                    return 0
        return proc.wait()
    except KeyboardInterrupt:
        _terminate_tree(proc)
        return 130
    finally:
        if saw_done and proc.poll() is None:
            _terminate_tree(proc)


def main() -> int:
    args = parse_args()
    return worker(args) if args.worker else supervise(args)


if __name__ == "__main__":
    raise SystemExit(main())
