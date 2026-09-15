#!/usr/bin/env python3
"""Replay one exact reward-beam path and expose per-frame 2-D geometry.

This is a diagnostic companion to the bounded reward beam probes.  It does not
search or change policy.  Given a comma-separated sequence of existing 4-frame
chunk names, it replays that path from a saved SMB1 scenario and prints Mario,
reward, momentum, and finite-difference target motion for every authoritative
Mesen frame.

Collection remains proven only by native capability state (for Star, an
increase in StarInvincibleTimer).  X/Y overlap printed here is diagnostic only.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# Import V2 for sticky reward-target observation outside the forward radar.
import smb1_reward_beam_probe_v2 as _v2  # noqa: F401
import smb1_reward_beam_probe as base

from fami_pixel.adapters.mesen import MesenCore, MesenLoadError, configure_standard_nes_controller, set_nes_controller_state
from fami_pixel.games.smb1 import GameEventType, derive_game_events, observation_from_state, read_smb1_state
from fami_pixel.games.smb1.reward_beam import (
    REWARD_BEAM_CHUNKS,
    buttons_for_chunk_frame,
    matching_reward,
    reward_collection_proven,
)


_DONE = "RewardPathReplay: DONE"
_CHUNKS = {chunk.name: chunk for chunk in REWARD_BEAM_CHUNKS}


def _signed_byte(value: int) -> int:
    value = int(value) & 0xFF
    return value - 0x100 if value & 0x80 else value


def _parse_path(text: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in text.split(",") if part.strip())
    if not names:
        raise argparse.ArgumentTypeError("path must contain at least one chunk name")
    unknown = [name for name in names if name not in _CHUNKS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown chunk(s): {', '.join(unknown)}; valid={', '.join(sorted(_CHUNKS))}"
        )
    return names


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Replay one exact SMB1 reward-beam path with per-frame geometry")
    p.add_argument("rom", type=Path)
    p.add_argument("scenario_dir", type=Path)
    p.add_argument("--target-reward", choices=("mushroom", "fire_flower", "star", "one_up"))
    p.add_argument("--path", required=True, type=_parse_path, help="comma-separated reward-beam chunk names")
    p.add_argument("--dll", type=Path, default=Path("build/mesen/MesenCore.dll"))
    p.add_argument("--home", type=Path, default=Path("build/mesen-home-reward-path-replay"))
    p.add_argument("--step-timeout", type=float, default=5.0)
    p.add_argument("--step-retries", type=int, default=1)
    args = p.parse_args()
    if args.step_timeout <= 0:
        p.error("--step-timeout must be > 0")
    if args.step_retries < 0:
        p.error("--step-retries must be >= 0")
    return args


def _fmt(value: int | None, width: int = 4) -> str:
    return "--".rjust(width) if value is None else f"{int(value):{width}d}"


def main() -> int:
    args = parse_args()
    scenario_dir = args.scenario_dir.expanduser().resolve()
    manifest, root_state = base._load_manifest(scenario_dir)
    target_reward = args.target_reward or manifest.get("selection_reward_type")
    if target_reward not in {"mushroom", "fire_flower", "star", "one_up"}:
        raise SystemExit("--target-reward is required when the scenario manifest has no reward type")

    core = MesenCore(args.dll)
    core.initialize_headless(args.home)
    configure_standard_nes_controller(core, port=1)
    if not core.load_rom(args.rom):
        raise SystemExit("LoadRom: FAIL")
    core.initialize_debugger()

    root_frame = int(manifest.get("native_frame"))
    root_x = int(manifest.get("mario_x"))
    base._restore(core, root_state, expected_frame=root_frame, expected_x=root_x)
    root_obs = observation_from_state(core.frame_count(), read_smb1_state(core))
    root_radar = base._radar(core, root_obs.mario_x_abs)
    root_reward = matching_reward(root_radar, str(target_reward))
    if root_reward is None:
        raise SystemExit(f"target reward {target_reward!r} is not tracked at the scenario root")

    baseline_status = int(root_radar.get("player_status", 0))
    baseline_star_timer = int(root_radar.get("star_invincible_timer", 0))

    print(f"Scenario   : {manifest.get('id')}", flush=True)
    print(
        f"Root       : frame={root_obs.native_frame_id} Mario=({root_obs.mario_x_abs},{root_obs.mario_y}) "
        f"target={target_reward} at=({root_reward.get('x')},{root_reward.get('y')}) "
        f"state={int(root_reward.get('state', 0))}",
        flush=True,
    )
    print(f"Path       : {' -> '.join(args.path)}", flush=True)
    print(
        "Columns    : idx frame chunk/ofs buttons | Mario X/Y vx/vy | target X/Y dx/dy dX/dY state | enemy timer",
        flush=True,
    )

    previous_obs = root_obs
    previous_target_x = int(root_reward.get("x", root_obs.mario_x_abs + int(root_reward.get("dx", 0))))
    previous_target_y = int(root_reward.get("y", root_obs.mario_y))
    row_index = 0

    try:
        with tempfile.TemporaryDirectory(prefix="fami-pixel-reward-path-") as tmp:
            retry_state = Path(tmp) / "chunk-start.mss"

            for chunk_index, chunk_name in enumerate(args.path, start=1):
                chunk = _CHUNKS[chunk_name]
                chunk_start_frame = int(previous_obs.native_frame_id)
                chunk_start_x = int(previous_obs.mario_x_abs)
                core.save_state_file(retry_state)
                completed = False

                for attempt in range(int(args.step_retries) + 1):
                    if attempt:
                        base._wait_for_debugger_stop(core, grace_s=min(1.0, float(args.step_timeout)))
                        base._restore(
                            core,
                            retry_state,
                            expected_frame=chunk_start_frame,
                            expected_x=chunk_start_x,
                        )
                        previous_obs = observation_from_state(core.frame_count(), read_smb1_state(core))

                    buffered: list[tuple] = []
                    local_target_x = previous_target_x
                    local_target_y = previous_target_y
                    try:
                        for offset in range(chunk.frame_count):
                            buttons = buttons_for_chunk_frame(chunk, offset)
                            set_nes_controller_state(core, 0, buttons)
                            timeout_s = float(args.step_timeout) * (2.0 if attempt else 1.0)
                            core.step_frame_sync(1, max(1, int(timeout_s * 1000.0)))
                            current = observation_from_state(core.frame_count(), read_smb1_state(core))
                            radar = base._radar(core, current.mario_x_abs)
                            reward = matching_reward(radar, str(target_reward))
                            enemy = radar.get("nearest_enemy_dx")
                            enemy_dx = None if enemy is None else int(enemy)

                            if reward is None:
                                target_x = target_y = dx = dy = dtx = dty = state = None
                            else:
                                target_x = int(reward.get("x", current.mario_x_abs + int(reward.get("dx", 0))))
                                target_y = int(reward.get("y", current.mario_y))
                                dx = int(reward.get("dx", target_x - current.mario_x_abs))
                                dy = target_y - int(current.mario_y)
                                dtx = target_x - local_target_x
                                dty = target_y - local_target_y
                                state = int(reward.get("state", 0))
                                local_target_x = target_x
                                local_target_y = target_y

                            events = derive_game_events(previous_obs, current)
                            died = any(event.kind == GameEventType.DIED for event in events)
                            won = any(event.kind == GameEventType.LEVEL_COMPLETED for event in events)
                            collected = reward_collection_proven(
                                str(target_reward),
                                baseline_player_status=baseline_status,
                                baseline_star_timer=baseline_star_timer,
                                radar=radar,
                            )
                            buffered.append(
                                (
                                    current,
                                    buttons,
                                    target_x,
                                    target_y,
                                    dx,
                                    dy,
                                    dtx,
                                    dty,
                                    state,
                                    enemy_dx,
                                    int(radar.get("star_invincible_timer", 0)),
                                    died,
                                    won,
                                    collected,
                                )
                            )
                            previous_obs = current
                            if died or won or collected:
                                break
                        completed = True
                    except MesenLoadError as exc:
                        if not base._is_native_step_timeout(exc):
                            raise
                        print(
                            f"STEP TIMEOUT : chunk={chunk_index}:{chunk_name} attempt={attempt + 1}/"
                            f"{int(args.step_retries) + 1}",
                            flush=True,
                        )
                        try:
                            set_nes_controller_state(core, 0, 0x00)
                        except Exception:
                            pass
                        if attempt >= int(args.step_retries):
                            print("UNRESOLVED : replay branch quarantined after native step timeout", flush=True)
                            return 3
                        continue

                    if completed:
                        for offset, item in enumerate(buffered):
                            (
                                current,
                                buttons,
                                target_x,
                                target_y,
                                dx,
                                dy,
                                dtx,
                                dty,
                                state,
                                enemy_dx,
                                timer,
                                died,
                                won,
                                collected,
                            ) = item
                            row_index += 1
                            print(
                                f"{row_index:03d} {current.native_frame_id:5d} {chunk_name:19s}/{offset} {buttons:02x} | "
                                f"{current.mario_x_abs:4d}/{current.mario_y:3d} "
                                f"{_signed_byte(current.player_x_speed):3d}/{_signed_byte(current.player_y_speed):3d} | "
                                f"{_fmt(target_x)}/{_fmt(target_y,3)} {_fmt(dx)}/{_fmt(dy,3)} "
                                f"{_fmt(dtx,3)}/{_fmt(dty,3)} {_fmt(state,3)} | "
                                f"{_fmt(enemy_dx,3)} {timer:3d}",
                                flush=True,
                            )
                            if target_x is not None:
                                previous_target_x = int(target_x)
                                previous_target_y = int(target_y)
                            if collected:
                                status = int(base._radar(core, current.mario_x_abs).get("player_status", 0))
                                print(
                                    f"COLLECTED  : frame={current.native_frame_id} path-prefix={chunk_index} chunks",
                                    flush=True,
                                )
                                print(
                                    f"PROOF      : player_status={baseline_status}->{status} "
                                    f"star_timer={baseline_star_timer}->{timer}",
                                    flush=True,
                                )
                                print(_DONE, flush=True)
                                return 0
                            if died:
                                print(f"DIED       : frame={current.native_frame_id}", flush=True)
                                print(_DONE, flush=True)
                                return 2
                            if won:
                                print(f"WIN        : frame={current.native_frame_id}", flush=True)
                                print(_DONE, flush=True)
                                return 0
                        break

            print("NO COLLECT : exact path completed without authoritative collection", flush=True)
            print(_DONE, flush=True)
            return 1
    finally:
        try:
            set_nes_controller_state(core, 0, 0x00)
            core.stop()
            core.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
