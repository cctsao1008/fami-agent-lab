"""Verified Mesen CE memory-read ABI for the pinned submodule revision."""

from __future__ import annotations

import ctypes

from .loader import MesenCore, MesenLoadError


# Core/Shared/MemoryType.h, pinned Mesen CE revision.
MEMORY_TYPE_NES_MEMORY = 8
MEMORY_TYPE_NES_INTERNAL_RAM = 48


def bind_memory_api(core: MesenCore) -> None:
    """Bind the exact debugger memory-read exports used by M0."""
    try:
        core._dll.GetMemorySize.argtypes = [ctypes.c_int]
        core._dll.GetMemorySize.restype = ctypes.c_uint32
        core._dll.GetMemoryValue.argtypes = [ctypes.c_int, ctypes.c_uint32]
        core._dll.GetMemoryValue.restype = ctypes.c_uint8
        core._dll.GetMemoryState.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint8)]
        core._dll.GetMemoryState.restype = None
    except AttributeError as exc:
        raise MesenLoadError("MesenCore.dll is missing debugger memory exports.") from exc


def get_memory_size(core: MesenCore, memory_type: int) -> int:
    if not core.is_debugger_running():
        raise MesenLoadError("initialize_debugger() must be called before memory reads.")
    bind_memory_api(core)
    return int(core._dll.GetMemorySize(memory_type))


def read_memory_value(core: MesenCore, memory_type: int, address: int) -> int:
    if not core.is_debugger_running():
        raise MesenLoadError("initialize_debugger() must be called before memory reads.")
    if address < 0:
        raise ValueError("address must be >= 0")
    bind_memory_api(core)
    return int(core._dll.GetMemoryValue(memory_type, address))


def read_nes_cpu_memory(core: MesenCore, address: int) -> int:
    """Read one byte from the NES CPU address space (MemoryType::NesMemory)."""
    if not 0 <= address <= 0xFFFF:
        raise ValueError("NES CPU address must be in range 0x0000..0xFFFF")
    return read_memory_value(core, MEMORY_TYPE_NES_MEMORY, address)


def read_nes_internal_ram(core: MesenCore) -> bytes:
    """Return the complete 2 KiB NES internal RAM image through Mesen's debugger."""
    size = get_memory_size(core, MEMORY_TYPE_NES_INTERNAL_RAM)
    buffer = (ctypes.c_uint8 * size)()
    core._dll.GetMemoryState(MEMORY_TYPE_NES_INTERNAL_RAM, buffer)
    return bytes(buffer)
