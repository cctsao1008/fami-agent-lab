"""Live reward-objective helpers for receding-horizon SMB1 control.

This module is intentionally small and policy-facing. Mesen remains authoritative
for transitions, death, and collection. The helpers here keep a bounded COLLECT
objective alive across short radar dropouts and select only fresh, exact-Mesen
reward-prefix responses from asynchronous shadow workers.
"""

from __future__ import annotations

from dataclasses import dataclass

from fami_pixel.adapters.mesen import NES_B, NES_LEFT, NES_RIGHT

from .reward_beam import RewardBeamChunk


REWARD_TYPES = {"mushroom", "fire_flower", "star", "one_up"}
DEFAULT_STICKY_TTL_FRAMES = 24


@dataclass
class StickyCollectObjective:
    """Track one selected reward target for a bounded number of native frames."""

    ttl_frames: int = DEFAULT_STICKY_TTL_FRAMES
    target_type: str | None = None
    acquired_frame: int | None = None
    last_seen_frame: int | None = None
    baseline_player_status: int = 0
    baseline_star_timer: int = 0

    def __post_init__(self) -> None:
        if self.ttl_frames < 0:
            raise ValueError("ttl_frames must be >= 0")

    def clear(self) -> None:
        self.target_type = None
        self.acquired_frame = None
        self.last_seen_frame = None
        self.baseline_player_status = 0
        self.baseline_star_timer = 0

    def _collection_proven(self, radar: dict) -> bool:
        if self.target_type in {"mushroom", "fire_flower"}:
            return int(radar.get("player_status", 0)) > int(self.baseline_player_status)
        if self.target_type == "star":
            return int(radar.get("star_invincible_timer", 0)) > int(self.baseline_star_timer)
        return False

    def update(
        self,
        *,
        frame: int,
        radar: dict,
        tracked_reward: dict | None,
    ) -> dict:
        """Return the current objective snapshot after observing one live frame.

        A native tracked reward acquires/refreshes the objective. If the reward
        briefly disappears, the objective survives for ``ttl_frames``. Proven
        capability change ends the objective immediately.
        """

        frame = int(frame)
        collected_type = None
        if self.target_type is not None and self._collection_proven(radar):
            collected_type = self.target_type
            self.clear()
            return {
                "mode": "PROGRESS",
                "target_type": None,
                "target": None,
                "collected_type": collected_type,
                "sticky": False,
            }

        observed_type = None
        if tracked_reward is not None:
            candidate = str(tracked_reward.get("type"))
            if candidate in REWARD_TYPES:
                observed_type = candidate

        if observed_type is not None:
            if self.target_type != observed_type:
                self.target_type = observed_type
                self.acquired_frame = frame
                self.baseline_player_status = int(radar.get("player_status", 0))
                self.baseline_star_timer = int(radar.get("star_invincible_timer", 0))
            self.last_seen_frame = frame
        elif self.target_type is not None:
            last_seen = self.last_seen_frame if self.last_seen_frame is not None else frame
            if frame - int(last_seen) > int(self.ttl_frames):
                self.clear()

        if self.target_type is None:
            return {
                "mode": "PROGRESS",
                "target_type": None,
                "target": None,
                "collected_type": collected_type,
                "sticky": False,
            }

        return {
            "mode": "COLLECT",
            "target_type": self.target_type,
            "target": dict(tracked_reward) if tracked_reward is not None else None,
            "collected_type": collected_type,
            "sticky": tracked_reward is None,
            "acquired_frame": self.acquired_frame,
            "last_seen_frame": self.last_seen_frame,
            "baseline_player_status": int(self.baseline_player_status),
            "baseline_star_timer": int(self.baseline_star_timer),
        }


def reward_chunk_schedule(chunk: RewardBeamChunk) -> list[dict[str, int]]:
    """Encode one proven 4-frame chunk plus an A-released continuation tail.

    V11 holds the final schedule segment while waiting for a replacement. The
    continuation therefore deliberately releases A after the exact prefix so a
    stale reward plan cannot turn into an unbounded jump hold.
    """

    schedule = [
        {"buttons": int(command.nes_buttons), "frames": int(command.frame_count)}
        for command in chunk.commands
    ]
    name = chunk.name
    if "left" in name:
        tail_buttons = NES_LEFT | NES_B
    elif "right" in name:
        tail_buttons = NES_RIGHT | NES_B
    else:
        tail_buttons = 0x00
    schedule.append({"buttons": int(tail_buttons), "frames": 1})
    return schedule


def select_fresh_reward_response(
    responses: list[dict],
    *,
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    target_type: str,
) -> dict | None:
    """Choose one fresh exact-Mesen reward prefix without a cohort barrier."""

    eligible: list[dict] = []
    for response in responses:
        if "error" in response:
            continue
        if str(response.get("planner_mode")) != "collect":
            continue
        if str(response.get("target_reward_type")) != str(target_type):
            continue
        if not bool(response.get("reward_prefix_safe", False)):
            continue
        try:
            generation = int(response.get("generation", -1))
            root_frame = int(response.get("root_frame", -1))
        except (TypeError, ValueError):
            continue
        age = int(current_frame) - root_frame
        if generation <= int(last_applied_generation) or age < 0 or age > int(freshness):
            continue
        item = dict(response)
        item["age"] = age
        eligible.append(item)

    if not eligible:
        return None

    newest_generation = max(int(item["generation"]) for item in eligible)
    newest = [item for item in eligible if int(item["generation"]) == newest_generation]

    def key(item: dict) -> tuple:
        collected = 1 if bool(item.get("reward_collected", False)) else 0
        raw = item.get("reward_key") or ()
        try:
            reward_key = tuple(int(value) for value in raw)
        except (TypeError, ValueError):
            reward_key = ()
        return (collected, reward_key, -float(item.get("compute_ms", 0.0)))

    return max(newest, key=key)
