"""Minimal, non-speculative loader for the Mesen CE interop DLL.

This module binds only ABI entries whose signatures have been verified in the
pinned Mesen CE source. Struct-heavy exports remain discovery-only until their
exact native layouts are audited.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Iterable


class MesenLoadError(RuntimeError):
    """Raised when MesenCore.dll cannot be loaded or a verified call fails."""


# Verified enum values from the pinned Mesen CE source.
CPU_TYPE_NES = 8
STEP_TYPE_PPU_FRAME = 6


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
            self._dll_directory = os.add_dll_directory(str(self.path.parent))
            self._dll = ctypes.WinDLL(str(self.path))
        except (OSError, AttributeError) as exc:
            raise MesenLoadError(f"Failed to load {self.path}: {exc}") from exc

        self._initialized = False
        self._debugger_initialized = False
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

            self._dll.InitializeDebugger.argtypes = []
            self._dll.InitializeDebugger.restype = None

            self._dll.ReleaseDebugger.argtypes = []
            self._dll.ReleaseDebugger.restype = None

            self._dll.IsDebuggerRunning.argtypes = []
            self._dll.IsDebuggerRunning.restype = ctypes.c_bool

            self._dll.IsExecutionStopped.argtypes = []
            self._dll.IsExecutionStopped.restype = ctypes.c_bool

            self._dll.ResumeExecution.argtypes = []
            self._dll.ResumeExecution.restype = None

            # CpuType is uint8_t in pinned source. StepType uses the default int
            # enum representation. The exported count is uint32_t.
            self._dll.Step.argtypes = [ctypes.c_uint8, ctypes.c_uint32, ctypes.c_int]
            self._dll.Step.restype = None

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
        return bool(self._dll.TestDll())

    def version(self) -> int:
        return int(self._dll.GetMesenVersion())

    def build_date(self) -> str:
        value = self._dll.GetMesenBuildDate()
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace")

    def initialize_headless(self, home_folder: str | os.PathLike[str]) -> None:
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
            True,
            True,
            True,
            True,
        )
        self._initialized = True

    def load_rom(self, rom_path: str | os.PathLike[str]) -> bool:
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

    def initialize_debugger(self) -> None:
        if not self._initialized:
            raise MesenLoadError(
                "initialize_headless() must be called before initialize_debugger()."
            )
        if self._released:
            raise MesenLoadError("MesenCore instance has already been released.")
        if self._debugger_initialized:
            return

        self._dll.InitializeDebugger()
        self._debugger_initialized = True

    def release_debugger(self) -> None:
        if self._debugger_initialized and not self._released:
            self._dll.ReleaseDebugger()
            self._debugger_initialized = False

    def is_debugger_running(self) -> bool:
        return bool(self._dll.IsDebuggerRunning())

    def is_execution_stopped(self) -> bool:
        return bool(self._dll.IsExecutionStopped())

    def resume_execution(self) -> None:
        self._dll.ResumeExecution()

    def step_ppu_frame(self, count: int = 1) -> None:
        """Request one or more NES PPU-frame debugger steps.

        `Debugger::Step()` installs a new step request. When execution is already
        stopped at the previous step boundary, that request does not advance the
        machine until `ResumeExecution()` is issued. Track the pre-call stopped
        state so repeated host-side frame stepping actually resumes execution.

        The first request made while the emulator is running needs no explicit
        resume; Mesen will stop when the requested PPU-frame count is reached.
        """
        if not self._debugger_initialized:
            raise MesenLoadError("initialize_debugger() must be called before stepping.")
        if count < 1:
            raise ValueError("count must be >= 1")

        was_stopped = self.is_execution_stopped()
        self._dll.Step(CPU_TYPE_NES, count, STEP_TYPE_PPU_FRAME)
        if was_stopped:
            self._dll.ResumeExecution()

    def stop(self) -> None:
        if self._initialized and not self._released:
            self._dll.Stop()
            self._debugger_initialized = False

    def release(self) -> None:
        if self._released:
            return
        if self._initialized:
            self._dll.Release()
        self._debugger_initialized = False
        self._released = True

    def __enter__(self) -> "MesenCore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def has_export(self, name: str) -> bool:
        try:
            getattr(self._dll, name)
        except AttributeError:
            return False
        return True

    def available_exports(self, names: Iterable[str] = M0_EXPORTS) -> dict[str, bool]:
        return {name: self.has_export(name) for name in names}
