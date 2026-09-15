"""Conservative enemy-cluster and landing-zone projection for SMB1.

This module intentionally does not pretend to be an exact ballistic model.
It projects structured enemy radar into a small, auditable heuristic used by the
live planner to answer a narrower question: if Mario commits to a normal running
jump now, is the empirically observed landing corridor occupied by enemies?

Mesen remains authoritative for the actual trajectory and collision outcome.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_CLUSTER_GAP_PX = 48
DEFAULT_LANDING_NEAR_PX = 96
DEFAULT_LANDING_FAR_PX = 160


@dataclass(frozen=True)
class EnemyCluster:
    start_dx: int
    end_dx: int
    count: int
    enemy_dxs: tuple[int, ...]

    @property
    def span_px(self) -> int:
        return self.end_dx - self.start_dx


@dataclass(frozen=True)
class LandingZoneAssessment:
    forward_enemy_count: int
    clusters: tuple[EnemyCluster, ...]
    nearest_cluster: EnemyCluster | None
    landing_near_px: int
    landing_far_px: int
    landing_enemy_count: int
    landing_enemy_dxs: tuple[int, ...]

    @property
    def landing_safe(self) -> bool:
        return self.landing_enemy_count == 0

    @property
    def landing_unsafe(self) -> bool:
        return not self.landing_safe

    def to_payload(self) -> dict:
        nearest = self.nearest_cluster
        return {
            "forward_enemy_count": self.forward_enemy_count,
            "enemy_cluster_count": len(self.clusters),
            "nearest_cluster_count": 0 if nearest is None else nearest.count,
            "nearest_cluster_start_dx": None if nearest is None else nearest.start_dx,
            "nearest_cluster_end_dx": None if nearest is None else nearest.end_dx,
            "nearest_cluster_span_px": None if nearest is None else nearest.span_px,
            "landing_corridor_start_dx": self.landing_near_px,
            "landing_corridor_end_dx": self.landing_far_px,
            "landing_enemy_count": self.landing_enemy_count,
            "landing_enemy_dxs": list(self.landing_enemy_dxs),
            "landing_safe": self.landing_safe,
            "landing_unsafe": self.landing_unsafe,
        }


def _forward_enemy_dxs(radar: dict) -> tuple[int, ...]:
    values: list[int] = []
    for enemy in radar.get("enemies") or ():
        try:
            dx = int(enemy.get("dx"))
        except (AttributeError, TypeError, ValueError):
            continue
        if dx >= 0:
            values.append(dx)
    return tuple(sorted(values))


def cluster_forward_enemies(
    radar: dict,
    *,
    cluster_gap_px: int = DEFAULT_CLUSTER_GAP_PX,
) -> tuple[EnemyCluster, ...]:
    """Group forward enemies whose adjacent separation is within one gap bound."""
    if cluster_gap_px < 0:
        raise ValueError("cluster_gap_px must be >= 0")

    dxs = _forward_enemy_dxs(radar)
    if not dxs:
        return ()

    groups: list[list[int]] = [[dxs[0]]]
    for dx in dxs[1:]:
        if dx - groups[-1][-1] <= cluster_gap_px:
            groups[-1].append(dx)
        else:
            groups.append([dx])

    return tuple(
        EnemyCluster(
            start_dx=group[0],
            end_dx=group[-1],
            count=len(group),
            enemy_dxs=tuple(group),
        )
        for group in groups
    )


def assess_landing_zone(
    radar: dict,
    *,
    cluster_gap_px: int = DEFAULT_CLUSTER_GAP_PX,
    landing_near_px: int = DEFAULT_LANDING_NEAR_PX,
    landing_far_px: int = DEFAULT_LANDING_FAR_PX,
) -> LandingZoneAssessment:
    """Assess whether forward enemies occupy the conservative landing corridor."""
    if landing_near_px < 0:
        raise ValueError("landing_near_px must be >= 0")
    if landing_far_px < landing_near_px:
        raise ValueError("landing_far_px must be >= landing_near_px")

    dxs = _forward_enemy_dxs(radar)
    clusters = cluster_forward_enemies(radar, cluster_gap_px=cluster_gap_px)
    landing = tuple(dx for dx in dxs if landing_near_px <= dx <= landing_far_px)
    return LandingZoneAssessment(
        forward_enemy_count=len(dxs),
        clusters=clusters,
        nearest_cluster=None if not clusters else clusters[0],
        landing_near_px=int(landing_near_px),
        landing_far_px=int(landing_far_px),
        landing_enemy_count=len(landing),
        landing_enemy_dxs=landing,
    )
