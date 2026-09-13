"""Minimal, non-speculative loader for the Mesen CE interop DLL.

This module intentionally binds only ABI entries whose signatures are simple and
verified in upstream Mesen CE. Struct- and enum-heavy exports are discovered but
left unbound until their exact native layouts are audited.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Iterable


class MesenLoadError(RuntimeError):
    """Raised when MesenCore.dll cannot be loaded or fails its ABI smoke test."""


# Exports relevant to M0. Presence can be checked without guessing struct layouts.
M0_EXPORTS: tuple[str, ...] = (
    "TestDll",
    "GetMesenVersion",
    "GetMesenBuildDate",
    "InitDll",
    "InitializeEmu",
    "LoadRom",
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
    """Thin handle around MesenCore.dll for ABI discovery and safe smoke tests."""

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

        self._bind_smoke_test_exports()

    def _bind_smoke_test_exports(self) -> None:
        try:
            self._dll.TestDll.argtypes = []
            self._dll.TestDll.restype = ctypes.c_bool

            self._dll.GetMesenVersion.argtypes = []
            self._dll.GetMesenVersion.restype = ctypes.c_uint32

            self._dll.GetMesenBuildDate.argtypes = []
            self._dll.GetMesenBuildDate.restype = ctypes.c_char_p
        except AttributeError as exc:
            raise MesenLoadError(
                "Loaded DLL does not expose the expected Mesen CE smoke-test ABI."
            ) from exc

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
