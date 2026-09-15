"""Authoritative forward-progress watchdog for live SMB1 control.

The watchdog deliberately observes only native frame count and Mario's decoded
world X.  Learned scores, radar classifications, and planner output are not
allowed to define whether the live machine is actually making progress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


WatchdogAction = Literal["ok", "recover", "abort"]


@dataclass(frozen=True)
class WatchdogDecision:
    action: WatchdogAction
    stagnant_frames: int
    progress_anchor_x: int
    max_x: int
    recovery_count: int


class NoProgressWatchdog:
    """Detect sustained lack of forward progress on the authoritative trajectory.

    Progress is acknowledged only after Mario exceeds the current progress anchor
    by ``min_progress_px``.  A soft recovery is emitted once per stall episode;
    if progress still does not resume by ``abort_after_frames``, the watchdog
    emits a hard abort.  Meaningful progress resets the episode.
    """

    def __init__(
        self,
        *,
        initial_frame: int,
        initial_x: int,
        min_progress_px: int = 8,
        recover_after_frames: int = 120,
        abort_after_frames: int = 300,
    ) -> None:
        if initial_frame < 0:
            raise ValueError("initial_frame must be >= 0")
        if min_progress_px <= 0:
            raise ValueError("min_progress_px must be > 0")
        if recover_after_frames <= 0:
            raise ValueError("recover_after_frames must be > 0")
        if abort_after_frames <= recover_after_frames:
            raise ValueError("abort_after_frames must be greater than recover_after_frames")

        self.min_progress_px = int(min_progress_px)
        self.recover_after_frames = int(recover_after_frames)
        self.abort_after_frames = int(abort_after_frames)

        self._last_seen_frame = int(initial_frame)
        self._last_progress_frame = int(initial_frame)
        self._progress_anchor_x = int(initial_x)
        self._max_x = int(initial_x)
        self._recovery_issued = False
        self._recovery_count = 0

    @property
    def progress_anchor_x(self) -> int:
        return self._progress_anchor_x

    @property
    def max_x(self) -> int:
        return self._max_x

    def observe(self, frame: int, x: int) -> WatchdogDecision:
        frame = int(frame)
        x = int(x)
        if frame < self._last_seen_frame:
            raise ValueError("frame must be monotonic")
        self._last_seen_frame = frame
        self._max_x = max(self._max_x, x)

        if self._max_x >= self._progress_anchor_x + self.min_progress_px:
            self._progress_anchor_x = self._max_x
            self._last_progress_frame = frame
            self._recovery_issued = False

        stagnant = max(0, frame - self._last_progress_frame)
        action: WatchdogAction = "ok"

        if stagnant >= self.abort_after_frames:
            action = "abort"
        elif stagnant >= self.recover_after_frames and not self._recovery_issued:
            self._recovery_issued = True
            self._recovery_count += 1
            action = "recover"

        return WatchdogDecision(
            action=action,
            stagnant_frames=stagnant,
            progress_anchor_x=self._progress_anchor_x,
            max_x=self._max_x,
            recovery_count=self._recovery_count,
        )
