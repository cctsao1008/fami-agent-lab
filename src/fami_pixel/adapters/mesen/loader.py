"""Minimal, non-speculative loader for the Mesen CE interop DLL.

This module binds only ABI entries whose signatures have been verified in the
pinned Mesen CE source. Struct- and enum-heavy exports remain discovery-only
until their exact native layouts are audited.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Iterable


class MesenLoadError(RuntimeError):
    """Raised when MesenCore.dll cannot be loaded or a verified call fails."""


# Exports relevant to M0. Presence can be checked without guessing struct layouts.
M0_EXPORTS: tuple[str, ...] = (
    "TestDll",
    "GetMesenVersion",
    "GetMesenBuildDate",
    "InitDll",
    "InitializeEmu",
    "LoadRom",
    "IsRunning",
    "Pause",
    "Resume",
    "IsPaused",
    "Stop",
    "Release",
    "InitializeDebugger",
    "ReleaseDebugger",
    "IsDebuggerRunning",
    "IsExecutionStopped",
    "ResumeExecution",
    "Step",
    "SetInputOverrides",
    "GetAvailableInputOverrides",
    "GetMemorySize",
    "GetMemoryState",
    "GetMemoryValue",
    "GetMemoryValues",
    "GetCpuState",
    "GetPpuState",
    "SaveState",
    "LoadState",
    "SaveStateFile",
    "LoadStateFile",
)


class MesenCore:
    """Thin handle around MesenCore.dll for M0 bring-up and ABI discovery."""

    def __init__(self, dll_path: str | os.PathLike[str]) -> None:
        if os.name != "nt":
            raise MesenLoadError("MesenCore.dll probing currently requires Windows.")

        self.path = Path(dll_path).expanduser().resolve()
        if not self.path.is_file():
            raise MesenLoadError(f"MesenCore.dll not found: {self.path}")

        try:
            # Keep dependent-DLL lookup local to the Mesen build directory.
            self._dll_directory = os.add_dll_directory(str(self.path.parent))
            self._dll = ctypes.WinDLL(str(self.path))
        except (OSError, AttributeError) as exc:
            raise MesenLoadError(f"Failed to load {self.path}: {exc}") from exc

        self._initialized = False
        self._released = False
        self._bind_verified_exports()

    def _bind_verified_exports(self) -> None:
        """Bind only exact signatures verified in the pinned upstream source."""
        try:
            self._dll.TestDll.argtypes = []
            self._dll.TestDll.restype = ctypes.c_bool

            self._dll.GetMesenVersion.argtypes = []
            self._dll.GetMesenVersion.restype = ctypes.c_uint32

            self._dll.GetMesenBuildDate.argtypes = []
            self._dll.GetMesenBuildDate.restype = ctypes.c_char_p

            self._dll.InitDll.argtypes = []
            self._dll.InitDll.restype = None

            self._dll.InitializeEmu.argtypes = [
                ctypes.c_char_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_bool,
                ctypes.c_bool,
                ctypes.c_bool,
                ctypes.c_bool,
            ]
            self._dll.InitializeEmu.restype = None

            self._dll.LoadRom.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
            self._dll.LoadRom.restype = ctypes.c_bool

            self._dll.IsRunning.argtypes = []
            self._dll.IsRunning.restype = ctypes.c_bool

            self._dll.IsPaused.argtypes = []
            self._dll.IsPaused.restype = ctypes.c_bool

            self._dll.Pause.argtypes = []
            self._dll.Pause.restype = None

            self._dll.Resume.argtypes = []
            self._dll.Resume.restype = None

            self._dll.Stop.argtypes = []
            self._dll.Stop.restype = None

            self._dll.Release.argtypes = []
            self._dll.Release.restype = None
        except AttributeError as exc:
            raise MesenLoadError(
                "Loaded DLL does not expose the expected verified Mesen CE ABI."
            ) from exc

    @staticmethod
    def _native_path(path: Path) -> bytes:
        """Encode a Windows path for Mesen's narrow-char interop API."""
        return os.fsencode(str(path))

    def smoke_test(self) -> bool:
        """Call upstream TestDll(); no emulator initialization is performed."""
        return bool(self._dll.TestDll())

    def version(self) -> int:
        """Return Mesen's numeric version from GetMesenVersion()."""
        return int(self._dll.GetMesenVersion())

    def build_date(self) -> str:
        """Return the native build date string exported by Mesen CE."""
        value = self._dll.GetMesenBuildDate()
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace")

    def initialize_headless(self, home_folder: str | os.PathLike[str]) -> None:
        """Initialize Mesen without renderer, audio, or host input devices."""
        if self._released:
            raise MesenLoadError("MesenCore instance has already been released.")
        if self._initialized:
            return

        home = Path(home_folder).expanduser().resolve()
        home.mkdir(parents=True, exist_ok=True)

        self._dll.InitDll()
        self._dll.InitializeEmu(
            self._native_path(home),
            None,
            None,
            True,   # softwareRenderer; inert without viewer/window handles
            True,   # noAudio
            True,   # noVideo
            True,   # noInput
        )
        self._initialized = True

    def load_rom(self, rom_path: str | os.PathLike[str]) -> bool:
        """Load a local ROM through Mesen's verified LoadRom export."""
        if not self._initialized:
            raise MesenLoadError("initialize_headless() must be called before load_rom().")

        rom = Path(rom_path).expanduser().resolve()
        if not rom.is_file():
            raise MesenLoadError(f"ROM not found: {rom}")

        return bool(self._dll.LoadRom(self._native_path(rom), None))

    def is_running(self) -> bool:
        return bool(self._dll.IsRunning())

    def is_paused(self) -> bool:
        return bool(self._dll.IsPaused())

    def pause(self) -> None:
        self._dll.Pause()

    def resume(self) -> None:
        self._dll.Resume()

    def stop(self) -> None:
        if self._initialized and not self._released:
            self._dll.Stop()

    def release(self) -> None:
        """Release the native emulator exactly once."""
        if self._released:
            return
        if self._initialized:
            self._dll.Release()
        self._released = True

    def __enter__(self) -> "MesenCore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def has_export(self, name: str) -> bool:
        """Return whether the loaded DLL exports *name*."""
        try:
            getattr(self._dll, name)
        except AttributeError:
            return False
        return True

    def available_exports(self, names: Iterable[str] = M0_EXPORTS) -> dict[str, bool]:
        """Probe a set of export names without binding uncertain ABI signatures."""
        return {name: self.has_export(name) for name in names}
