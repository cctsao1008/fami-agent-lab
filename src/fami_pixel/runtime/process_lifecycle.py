"""Small dependency-free helpers for live planner process ownership.

The live SMB1 planner uses one authority process plus multiple Python shadow
workers. Workers must not survive their authority indefinitely, one run's IPC
files must never be reused by another run, and Windows field runs should have an
OS-level last-resort ownership boundary in addition to Python-side teardown.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import threading
import time


_PARENT_ENV = "FAMI_PIXEL_AUTHORITY_PID"
_JOB_ENV = "FAMI_PIXEL_WINDOWS_JOB_NAME"


def isolated_run_dir(base: str | os.PathLike[str]) -> Path:
    """Create and return a unique per-authority runtime directory."""
    root = Path(base).expanduser().resolve()
    path = root / f"run-{os.getpid()}-{time.time_ns()}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def parent_process_alive(pid: int) -> bool:
    """Return whether *pid* still names a live process without extra deps."""
    pid = int(pid)
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True

    if os.name == "nt":
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return int(kernel32.WaitForSingleObject(handle, 0)) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def authority_pid_from_env() -> int | None:
    value = os.environ.get(_PARENT_ENV)
    if not value:
        return None
    try:
        pid = int(value)
    except ValueError:
        return None
    return pid if pid > 0 else None


def leased_worker_environment(parent_pid: int, *, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env[_PARENT_ENV] = str(int(parent_pid))
    return env


def start_parent_lease_monitor(
    parent_pid: int | None,
    *,
    interval_s: float = 0.25,
) -> threading.Thread | None:
    """Exit the current worker promptly if its authority parent disappears."""
    if parent_pid is None:
        return None
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0")

    def monitor() -> None:
        while parent_process_alive(parent_pid):
            time.sleep(interval_s)
        os._exit(0)

    thread = threading.Thread(
        target=monitor,
        name=f"fami-pixel-parent-lease-{parent_pid}",
        daemon=True,
    )
    thread.start()
    return thread


def windows_job_name_for_run(*, pid: int | None = None, stamp_ns: int | None = None) -> str:
    """Return a unique local Windows Job Object name for one supervisor run."""
    owner = os.getpid() if pid is None else int(pid)
    stamp = time.time_ns() if stamp_ns is None else int(stamp_ns)
    return f"Local\\FamiPixel-{owner}-{stamp}"


def windows_job_environment(job_name: str, *, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env[_JOB_ENV] = str(job_name)
    return env


def windows_job_name_from_env() -> str | None:
    value = os.environ.get(_JOB_ENV)
    return value if value else None


class WindowsKillOnCloseJob:
    """Minimal Windows Job Object wrapper using only ctypes.

    The supervisor owns the durable job handle. The authority process opens the
    named job, assigns itself, and immediately closes its temporary handle. Its
    shadow children inherit job membership. Closing the supervisor handle applies
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE to the complete process group, including
    a native-teardown-stalled authority process.
    """

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_ASSIGN_PROCESS = 0x0001
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def __init__(self, name: str, handle: int):
        self.name = str(name)
        self._handle = int(handle)

    @classmethod
    def create(cls, name: str | None = None) -> "WindowsKillOnCloseJob | None":
        if os.name != "nt":
            return None
        name = windows_job_name_for_run() if name is None else str(name)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int

        handle = kernel32.CreateJobObjectW(None, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = cls._EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = cls.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            cls.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        return cls(name, int(handle))

    def close(self) -> None:
        handle = self._handle
        if not handle:
            return
        self._handle = 0
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))

    def __enter__(self) -> "WindowsKillOnCloseJob":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def join_windows_job_from_env() -> bool:
    """Assign the current process to the supervisor-owned named Job Object."""
    if os.name != "nt":
        return False
    name = windows_job_name_from_env()
    if not name:
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenJobObjectW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenJobObjectW.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p

    handle = kernel32.OpenJobObjectW(
        WindowsKillOnCloseJob.JOB_OBJECT_ASSIGN_PROCESS,
        False,
        name,
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenJobObjectW failed for {name}")
    try:
        ok = kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess())
        if not ok:
            raise OSError(ctypes.get_last_error(), f"AssignProcessToJobObject failed for {name}")
        return True
    finally:
        kernel32.CloseHandle(handle)
