from __future__ import annotations

import os

import pytest

from fami_pixel.adapters.mesen.loader import MesenCore, MesenLoadError


def test_missing_dll_fails_before_loading(tmp_path) -> None:
    if os.name != "nt":
        pytest.skip("MesenCore.dll probing is Windows-only")

    missing = tmp_path / "MesenCore.dll"
    with pytest.raises(MesenLoadError, match="not found"):
        MesenCore(missing)
