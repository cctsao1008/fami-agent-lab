"""Verified Mesen CE controller override ABI for the pinned submodule revision."""

from __future__ import annotations

import ctypes

from .loader import MesenCore, MesenLoadError


class DebugControllerState(ctypes.Structure):
    """ctypes mirror of Core/Debugger/DebugTypes.h::DebugControllerState.

    The pinned Mesen CE source defines 14 consecutive C++ bool fields. MSVC bool
    is one byte, and ctypes.c_bool matches that representation for this ABI.
    """

    _fields_ = [
        ("A", ctypes.c_bool),
        ("B", ctypes.c_bool),
        ("X", ctypes.c_bool),
        ("Y", ctypes.c_bool),
        ("L", ctypes.c_bool),
        ("R", ctypes.c_bool),
        ("U", ctypes.c_bool),
        ("D", ctypes.c_bool),
        ("Up", ctypes.c_bool),
        ("Down", ctypes.c_bool),
        ("Left", ctypes.c_bool),
        ("Right", ctypes.c_bool),
        ("Select", ctypes.c_bool),
        ("Start", ctypes.c_bool),
    ]


DEBUG_CONTROLLER_STATE_SIZE = ctypes.sizeof(DebugControllerState)
if DEBUG_CONTROLLER_STATE_SIZE != 14:
    raise RuntimeError(
        f"Unexpected DebugControllerState size: {DEBUG_CONTROLLER_STATE_SIZE}; expected 14"
    )


def bind_controller_api(core: MesenCore) -> None:
    """Bind the exact pinned-source debugger controller ABI on *core*."""
    try:
        core._dll.SetInputOverrides.argtypes = [ctypes.c_uint32, DebugControllerState]
        core._dll.SetInputOverrides.restype = None
        core._dll.GetAvailableInputOverrides.argtypes = [ctypes.POINTER(ctypes.c_uint8)]
        core._dll.GetAvailableInputOverrides.restype = None
    except AttributeError as exc:
        raise MesenLoadError("MesenCore.dll is missing controller override exports.") from exc


def available_input_overrides(core: MesenCore) -> tuple[bool, ...]:
    """Return the eight debugger input slots reported by Mesen."""
    bind_controller_api(core)
    slots = (ctypes.c_uint8 * 8)()
    core._dll.GetAvailableInputOverrides(slots)
    return tuple(bool(value) for value in slots)


def set_input_override(core: MesenCore, index: int, state: DebugControllerState) -> None:
    """Set one debugger input override slot using the verified by-value struct ABI."""
    if not core.is_debugger_running():
        raise MesenLoadError("initialize_debugger() must be called before input override.")
    if not 0 <= index < 8:
        raise ValueError("controller override index must be in range 0..7")
    bind_controller_api(core)
    core._dll.SetInputOverrides(index, state)


def released_state() -> DebugControllerState:
    """Return an all-false controller state."""
    return DebugControllerState()


def right_state(*, jump: bool = False) -> DebugControllerState:
    """Return RIGHT, optionally with NES A pressed for jumping."""
    state = DebugControllerState()
    state.Right = True
    state.A = jump
    return state
