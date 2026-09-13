"""Safe raw NES framebuffer access for the fami-pixel Mesen fork."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from .loader import MesenCore, MesenLoadError

NES_FRAME_WIDTH = 256
NES_FRAME_HEIGHT = 240
NES_FRAME_PIXEL_COUNT = NES_FRAME_WIDTH * NES_FRAME_HEIGHT


@dataclass(frozen=True)
class NesRawFrame:
    """Caller-owned snapshot of Mesen's canonical raw NES PPU frame.

    `pixels` contains one uint16_t word per pixel in row-major order.
    Bits 0..5 are the NES palette color index and bits 6..8 are emphasis bits.
    """

    frame_count: int
    width: int
    height: int
    pixels: tuple[int, ...]


def bind_fami_pixel_video_api(core: MesenCore) -> None:
    try:
        core._dll.FamiPixelCopyNesFrame.argtypes = [
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        core._dll.FamiPixelCopyNesFrame.restype = ctypes.c_int32
    except AttributeError as exc:
        raise MesenLoadError(
            "MesenCore.dll is missing the fami-pixel NES framebuffer export. "
            "Rebuild the pinned cctsao1008/MesenCE fork."
        ) from exc


def copy_nes_raw_frame(core: MesenCore) -> NesRawFrame:
    """Copy the canonical raw NES PPU frame while debugger execution is stopped."""
    bind_fami_pixel_video_api(core)

    buffer_type = ctypes.c_uint16 * NES_FRAME_PIXEL_COUNT
    buffer = buffer_type()
    width = ctypes.c_uint32()
    height = ctypes.c_uint32()
    frame_count = ctypes.c_uint32()

    status = int(
        core._dll.FamiPixelCopyNesFrame(
            buffer,
            NES_FRAME_PIXEL_COUNT,
            ctypes.byref(width),
            ctypes.byref(height),
            ctypes.byref(frame_count),
        )
    )
    if status != 0:
        messages = {
            1: "emulator is not running",
            2: "NES framebuffer is unavailable or has an unexpected geometry",
            3: "native output buffer was null",
            4: "native output capacity was too small",
            5: "debugger is not initialized",
            6: "execution must be stopped before copying the framebuffer",
        }
        detail = messages.get(status, "unknown native error")
        raise MesenLoadError(f"FamiPixelCopyNesFrame failed ({status}: {detail}).")

    if width.value != NES_FRAME_WIDTH or height.value != NES_FRAME_HEIGHT:
        raise MesenLoadError(
            f"Unexpected NES framebuffer geometry: {width.value}x{height.value}."
        )

    return NesRawFrame(
        frame_count=int(frame_count.value),
        width=int(width.value),
        height=int(height.value),
        pixels=tuple(int(value) for value in buffer),
    )
