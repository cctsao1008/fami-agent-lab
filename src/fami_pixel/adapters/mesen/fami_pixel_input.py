"""Native deterministic NES input injection for the fami-pixel Mesen fork."""

from __future__ import annotations

import ctypes

from .loader import MesenCore, MesenLoadError


NES_A = 0x01
NES_B = 0x02
NES_SELECT = 0x04
NES_START = 0x08
NES_UP = 0x10
NES_DOWN = 0x20
NES_LEFT = 0x40
NES_RIGHT = 0x80


def bind_fami_pixel_input_api(core: MesenCore) -> None:
    try:
        core._dll.FamiPixelSetNesControllerState.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint8,
        ]
        core._dll.FamiPixelSetNesControllerState.restype = ctypes.c_int32
        core._dll.FamiPixelGetNesControllerState.argtypes = [ctypes.c_uint32]
        core._dll.FamiPixelGetNesControllerState.restype = ctypes.c_int32
    except AttributeError as exc:
        raise MesenLoadError(
            "MesenCore.dll is missing the fami-pixel native input exports. "
            "Rebuild the pinned cctsao1008/MesenCE fork."
        ) from exc


def set_nes_controller_state(core: MesenCore, port: int, buttons: int) -> None:
    """Set an exact NES controller byte through Mesen's IInputProvider path.

    Native byte layout follows Mesen NesController::ToByte():
    A=bit0, B=bit1, Select=bit2, Start=bit3,
    Up=bit4, Down=bit5, Left=bit6, Right=bit7.
    """
    if not 0 <= port <= 1:
        raise ValueError("NES controller port must be 0 or 1")
    if not 0 <= buttons <= 0xFF:
        raise ValueError("NES controller state must fit in one byte")

    bind_fami_pixel_input_api(core)
    status = int(core._dll.FamiPixelSetNesControllerState(port, buttons))
    if status != 0:
        messages = {
            1: "emulator is not running",
            2: "invalid NES controller port",
        }
        detail = messages.get(status, "unknown native error")
        raise MesenLoadError(
            f"FamiPixelSetNesControllerState failed ({status}: {detail})."
        )


def get_nes_controller_state(core: MesenCore, port: int = 0) -> int:
    """Read the actual current byte stored in the emulated NES controller."""
    if not 0 <= port <= 1:
        raise ValueError("NES controller port must be 0 or 1")
    bind_fami_pixel_input_api(core)
    value = int(core._dll.FamiPixelGetNesControllerState(port))
    if value < 0:
        raise MesenLoadError(
            "FamiPixelGetNesControllerState could not resolve an active NES controller."
        )
    return value
