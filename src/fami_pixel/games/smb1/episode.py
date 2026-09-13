"""Episode accounting for the SMB1 M1 environment.

Episode results are experiment projections assembled from structured observations
and derived game events. They do not replace Mesen or the SMB1 decoder as state
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .events import GameEvent, GameEventType
from .observation import Smb1Observation


class EpisodeTermination(str, Enum):
    DEATH = "death"
    LEVEL_COMPLETE = "level_complete"
    TIMEOUT = "timeout"
    EXPLICIT_RESET = "explicit_reset"


@dataclass(frozen=True)
class EpisodeResult:
    termination: EpisodeTermination
    start_frame_id: int
    end_frame_id: int
    elapsed_frames: int
    start_x: int
    end_x: int
    max_x: int
    net_progress: int
    event_count: int
    moved_events: int
    jump_events: int
    landing_events: int


class EpisodeAccumulator:
    """Accumulate auditable episode metrics from observations and events."""

    def __init__(self, initial: Smb1Observation) -> None:
        self._initial = initial
        self._latest = initial
        self._max_x = initial.mario_x_abs
        self._event_count = 0
        self._moved_events = 0
        self._jump_events = 0
        self._landing_events = 0
        self._finished = False

    def record(
        self,
        observation: Smb1Observation,
        events: tuple[GameEvent, ...],
    ) -> None:
        if self._finished:
            raise RuntimeError("cannot record after episode result is finalized")
        if observation.native_frame_id <= self._latest.native_frame_id:
            raise ValueError("episode observations must advance native_frame_id")

        self._latest = observation
        self._max_x = max(self._max_x, observation.mario_x_abs)
        self._event_count += len(events)
        for event in events:
            if event.kind == GameEventType.MOVED:
                self._moved_events += 1
            elif event.kind == GameEventType.JUMP_STARTED:
                self._jump_events += 1
            elif event.kind == GameEventType.LANDED:
                self._landing_events += 1

    def finish(self, termination: EpisodeTermination) -> EpisodeResult:
        if self._finished:
            raise RuntimeError("episode result already finalized")
        self._finished = True

        start_frame = self._initial.native_frame_id
        end_frame = self._latest.native_frame_id
        return EpisodeResult(
            termination=termination,
            start_frame_id=start_frame,
            end_frame_id=end_frame,
            elapsed_frames=end_frame - start_frame,
            start_x=self._initial.mario_x_abs,
            end_x=self._latest.mario_x_abs,
            max_x=self._max_x,
            net_progress=self._latest.mario_x_abs - self._initial.mario_x_abs,
            event_count=self._event_count,
            moved_events=self._moved_events,
            jump_events=self._jump_events,
            landing_events=self._landing_events,
        )
