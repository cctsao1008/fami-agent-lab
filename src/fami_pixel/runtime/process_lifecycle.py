"""Small dependency-free helpers for live planner process ownership.

The live SMB1 planner uses one authority process plus multiple Python shadow
workers.  Workers must not survive their authority indefinitely, and one run's
IPC files must never be reused by another run.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import threading
import time


_PARENT_ENV = "FAMI_PIXEL_AUTHORITY_PID"


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
        # SYNCHRONIZE is sufficient for WaitForSingleObject and does not require
        # broad process privileges.
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
        # A shadow worker has no useful standalone state once authority is gone.
        # os._exit avoids debugger/native teardown hangs in an orphan process.
        os._exit(0)

    thread = threading.Thread(
        target=monitor,
        name=f"fami-pixel-parent-lease-{parent_pid}",
        daemon=True,
    )
    thread.start()
    return thread
