"""Authoritative event-horizon trajectory evaluation for SMB1.

This module is the first executable building block of the forward-model planner
tracked by GitHub issue #32. It deliberately does not guess Mario physics.
Instead, callers restore a Mesen save-state branch and this evaluator applies a
bounded action sequence until a meaningful authoritative event occurs.

The evaluator is branch-local: it mutates the supplied Mesen core. Callers that
compare multiple trajectories must restore the same root checkpoint before each
call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fami_pixel.adapters.mesen import MesenCore, set_nes_controller_state

from .actions import ActionCommand, Smb1Action, action_to_nes_buttons
from .events import GameEventType, derive_game_events
from .observation import Smb1Observation, observation_from_state
from .radar import read_smb1_radar
from .state import read_smb1_state


class TrajectoryEvent(str, Enum):
    """First meaningful event that ended one simulated branch."""

    DEATH = "death"
    WIN = "win"
    REWARD_COLLECTED = "reward_collected"
    CAPABILITY_CHANGED = "capability_changed"
    LANDED = "landed"
    HORIZON = "horizon"


@dataclass(frozen=True)
class TrajectoryPlan:
    """A bounded action prefix followed by one tail action until an event."""

    name: str
    commands: tuple[ActionCommand, ...]
    tail_action: Smb1Action = Smb1Action.NOOP

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("trajectory name must be non-empty")

    @property
    def prefix_frames(self) -> int:
        return sum(command.frame_count for command in self.commands)


@dataclass(frozen=True)
class TrajectoryResult:
    """Auditable outcome from one exact Mesen branch."""

    plan: TrajectoryPlan
    event: TrajectoryEvent
    frames_simulated: int
    start_frame: int
    end_frame: int
    start_x: int
    end_x: int
    max_x: int
    start_y: int
    end_y: int
    airborne_seen: bool
    landed: bool
    died: bool
    won: bool
    capability_changed: bool
    reward_collected: bool
    target_reward_type: str | None
    target_dx_start: int | None
    target_dx_end: int | None
    player_status_start: int
    player_status_end: int
    star_timer_start: int
    star_timer_end: int

    @property
    def progress(self) -> int:
        return self.end_x - self.start_x

    @property
    def max_progress(self) -> int:
        return self.max_x - self.start_x

    @property
    def target_approach(self) -> int | None:
        if self.target_dx_start is None or self.target_dx_end is None:
            return None
        return self.target_dx_start - self.target_dx_end


def _signed_target_dx(radar_payload: dict, reward_type: str | None) -> int | None:
    if reward_type is None:
        return None
    values: list[int] = []
    for reward in radar_payload.get("rewards") or ():
        if str(reward.get("type")) != reward_type:
            continue
        try:
            values.append(int(reward.get("dx")))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return min(values, key=lambda value: abs(value))


def _capability_changed(start_radar: dict, current_radar: dict) -> bool:
    return (
        int(current_radar.get("player_status", 0))
        != int(start_radar.get("player_status", 0))
        or int(current_radar.get("star_invincible_timer", 0))
        > int(start_radar.get("star_invincible_timer", 0))
    )


def _target_reward_collected(
    reward_type: str | None,
    start_radar: dict,
    current_radar: dict,
) -> bool:
    """Return only collection evidence proven by current native capability state.

    Mushroom / Fire Flower collection is proven by PlayerStatus increasing.
    Star collection is proven by the invincibility timer increasing. The native
    radar does not yet expose an authoritative lives counter, so 1-Up collection
    is intentionally not claimed here merely because its object disappears.
    """

    if reward_type in {"mushroom", "fire_flower"}:
        return int(current_radar.get("player_status", 0)) > int(
            start_radar.get("player_status", 0)
        )
    if reward_type == "star":
        return int(current_radar.get("star_invincible_timer", 0)) > int(
            start_radar.get("star_invincible_timer", 0)
        )
    return False


def _buttons_for_frame(plan: TrajectoryPlan, frame_offset: int) -> int:
    remaining = int(frame_offset)
    for command in plan.commands:
        if remaining < command.frame_count:
            return command.nes_buttons
        remaining -= command.frame_count
    return action_to_nes_buttons(plan.tail_action)


def evaluate_mesen_trajectory(
    core: MesenCore,
    plan: TrajectoryPlan,
    *,
    max_horizon_frames: int = 96,
    step_timeout_s: float = 2.0,
    target_reward_type: str | None = None,
    start_observation: Smb1Observation | None = None,
    stop_on_landing: bool = True,
) -> TrajectoryResult:
    """Simulate one trajectory until an authoritative event or hard horizon.

    The caller is responsible for restoring the desired branch root before this
    function is called. Only port 0 controller state and emulated state advance
    are modified here.

    ``stop_on_landing=False`` is useful for explicit COLLECT objectives: a branch
    may land safely and still need several more frames to intercept the target.
    Landing evidence is retained in the result even when it is not terminal.
    """

    if max_horizon_frames <= 0:
        raise ValueError("max_horizon_frames must be > 0")
    if step_timeout_s <= 0:
        raise ValueError("step_timeout_s must be > 0")

    if start_observation is None:
        start_observation = observation_from_state(core.frame_count(), read_smb1_state(core))

    start_radar = read_smb1_radar(
        core,
        player_x=start_observation.mario_x_abs,
    ).to_payload()
    previous = start_observation
    current = start_observation
    current_radar = start_radar
    max_x = start_observation.mario_x_abs
    airborne_seen = start_observation.player_state == 1
    event = TrajectoryEvent.HORIZON
    landed = False
    died = False
    won = False
    capability_changed = False
    reward_collected = False
    frames_simulated = 0
    target_dx_start = _signed_target_dx(start_radar, target_reward_type)

    try:
        for offset in range(max_horizon_frames):
            buttons = _buttons_for_frame(plan, offset)
            set_nes_controller_state(core, 0, buttons)
            core.step_frame_sync(1, max(1, int(step_timeout_s * 1000.0)))
            frames_simulated += 1

            current = observation_from_state(core.frame_count(), read_smb1_state(core))
            current_radar = read_smb1_radar(
                core,
                player_x=current.mario_x_abs,
            ).to_payload()
            max_x = max(max_x, current.mario_x_abs)
            if current.player_state == 1:
                airborne_seen = True

            events = derive_game_events(previous, current)
            died = any(item.kind == GameEventType.DIED for item in events)
            won = any(item.kind == GameEventType.LEVEL_COMPLETED for item in events)
            landed_now = airborne_seen and any(
                item.kind == GameEventType.LANDED for item in events
            )
            if landed_now:
                landed = True
            capability_changed = _capability_changed(start_radar, current_radar)
            reward_collected = _target_reward_collected(
                target_reward_type,
                start_radar,
                current_radar,
            )

            # Terminal ordering is deliberate. Death/win dominate everything;
            # a proven target collection dominates a generic capability change.
            if died:
                event = TrajectoryEvent.DEATH
            elif won:
                event = TrajectoryEvent.WIN
            elif reward_collected:
                event = TrajectoryEvent.REWARD_COLLECTED
            elif capability_changed:
                event = TrajectoryEvent.CAPABILITY_CHANGED
            elif landed_now and stop_on_landing:
                event = TrajectoryEvent.LANDED
            else:
                previous = current
                continue
            break
    finally:
        try:
            set_nes_controller_state(core, 0, 0x00)
        except Exception:
            # A terminal/native teardown edge must not erase the branch result.
            pass

    target_dx_end = _signed_target_dx(current_radar, target_reward_type)
    return TrajectoryResult(
        plan=plan,
        event=event,
        frames_simulated=frames_simulated,
        start_frame=start_observation.native_frame_id,
        end_frame=current.native_frame_id,
        start_x=start_observation.mario_x_abs,
        end_x=current.mario_x_abs,
        max_x=max_x,
        start_y=start_observation.mario_y,
        end_y=current.mario_y,
        airborne_seen=airborne_seen,
        landed=landed,
        died=died,
        won=won,
        capability_changed=capability_changed,
        reward_collected=reward_collected,
        target_reward_type=target_reward_type,
        target_dx_start=target_dx_start,
        target_dx_end=target_dx_end,
        player_status_start=int(start_radar.get("player_status", 0)),
        player_status_end=int(current_radar.get("player_status", 0)),
        star_timer_start=int(start_radar.get("star_invincible_timer", 0)),
        star_timer_end=int(current_radar.get("star_invincible_timer", 0)),
    )


def trajectory_outcome_key(result: TrajectoryResult) -> tuple[int, int, int, int]:
    """Return a transparent lexicographic baseline ordering for branch results."""

    outcome_class = {
        TrajectoryEvent.DEATH: 0,
        TrajectoryEvent.HORIZON: 1,
        TrajectoryEvent.LANDED: 3,
        TrajectoryEvent.CAPABILITY_CHANGED: 4,
        TrajectoryEvent.REWARD_COLLECTED: 5,
        TrajectoryEvent.WIN: 6,
    }[result.event]
    target_approach = result.target_approach
    return (
        outcome_class,
        0 if target_approach is None else target_approach,
        result.max_progress,
        result.progress,
    )
