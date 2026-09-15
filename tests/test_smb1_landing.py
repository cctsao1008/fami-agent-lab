import pytest

from fami_pixel.games.smb1.landing import (
    assess_landing_zone,
    cluster_forward_enemies,
)


def _radar(*dxs):
    return {
        "enemies": [
            {"slot": i, "id": 0x06, "state": 0, "x": 1000 + dx, "y": 176, "dx": dx}
            for i, dx in enumerate(dxs)
        ]
    }


def test_single_near_enemy_does_not_make_landing_corridor_unsafe():
    assessment = assess_landing_zone(_radar(42))

    assert assessment.forward_enemy_count == 1
    assert assessment.nearest_cluster is not None
    assert assessment.nearest_cluster.count == 1
    assert assessment.landing_enemy_count == 0
    assert assessment.landing_safe is True


def test_multi_enemy_scene_marks_enemies_inside_landing_corridor():
    assessment = assess_landing_zone(_radar(42, 65, 126, 150))

    assert assessment.forward_enemy_count == 4
    assert len(assessment.clusters) == 2
    assert assessment.clusters[0].enemy_dxs == (42, 65)
    assert assessment.clusters[1].enemy_dxs == (126, 150)
    assert assessment.landing_enemy_dxs == (126, 150)
    assert assessment.landing_enemy_count == 2
    assert assessment.landing_unsafe is True


def test_cluster_gap_is_explicit_and_tunable():
    clusters = cluster_forward_enemies(_radar(40, 70, 125), cluster_gap_px=60)

    assert len(clusters) == 1
    assert clusters[0].count == 3
    assert clusters[0].start_dx == 40
    assert clusters[0].end_dx == 125


def test_behind_enemies_are_not_part_of_forward_landing_assessment():
    assessment = assess_landing_zone(_radar(-12, 110))

    assert assessment.forward_enemy_count == 1
    assert assessment.landing_enemy_dxs == (110,)


def test_landing_corridor_validation():
    with pytest.raises(ValueError):
        assess_landing_zone(_radar(100), landing_near_px=120, landing_far_px=80)
