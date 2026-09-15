from types import SimpleNamespace

import pytest

from fami_pixel.telemetry import format_radar_strip, raw_frame_to_png


def test_radar_strip_places_forward_hazards():
    strip = format_radar_strip(48, 160, 96, lookahead_px=192, width=24)

    assert strip.startswith("[M] ")
    assert "E" in strip
    assert "G" in strip
    assert "O" in strip
    assert strip.index("E") < strip.index("O") < strip.index("G")


def test_radar_strip_places_reward_marker():
    strip = format_radar_strip(
        None,
        None,
        None,
        reward_dx=48,
        reward_marker="S",
        lookahead_px=192,
        width=24,
    )

    assert strip.startswith("[M] ")
    assert "S" in strip


def test_radar_strip_ignores_missing_and_behind_objects():
    strip = format_radar_strip(-8, None, None, lookahead_px=192, width=12)

    assert strip == "[M] ------------"


def test_radar_strip_validates_geometry():
    with pytest.raises(ValueError):
        format_radar_strip(None, None, None, lookahead_px=0)


def test_raw_frame_to_png_emits_png_signature():
    frame = SimpleNamespace(width=2, height=2, pixels=(0, 1, 2, 3))
    png = raw_frame_to_png(frame)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in png
    assert png.endswith(b"IEND\xaeB`\x82")
