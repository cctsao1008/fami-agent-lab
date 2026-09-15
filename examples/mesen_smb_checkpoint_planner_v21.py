#!/usr/bin/env python3
"""V21 live planner: V20 gameplay plus supervisor-side process hygiene.

V20 preserves per-run IPC isolation and shadow-worker parent leases, but Windows
field runs still showed one Python shadow process occasionally remaining after a
normal planner exit.  V21 keeps V20 control semantics unchanged and adds a final
supervisor-owned cleanup fence:

- capture the exact shadow PIDs reported by the authority process,
- let the authority perform its normal graceful teardown first,
- after the authority exits (or is force-terminated), verify those PIDs,
- force-kill only the still-live shadow PIDs from this run,
- report whether the process census is clean before the supervisor returns.

This is intentionally a lifecycle wrapper.  It does not change radar, reward,
landing-zone, watchdog, learned-risk, or Mesen authority semantics.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from fami_pixel.runtime.process_lifecycle import parent_process_alive

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v20 as v20


PLANNER_NAME = "v21-process-hygiene"
_SHADOW_PID_RE = re.compile(r"Shadow PIDs\s*:\s*(?P<pids>[0-9, ]+)")
_PID_EXIT_GRACE_S = 1.0


def _parse_shadow_pids(line: str) -> tuple[int, ...]:
    match = _SHADOW_PID_RE.search(str(line))
    if match is None:
        return ()
    values: list[int] = []
    for token in match.group("pids").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid > 0:
            values.append(pid)
    return tuple(values)


def _terminate_pid_tree(pid: int) -> None:
    """Best-effort last-resort termination for one PID captured from this run."""
    pid = int(pid)
    if pid <= 0 or not parent_process_alive(pid):
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    deadline = time.monotonic() + _PID_EXIT_GRACE_S
    while time.monotonic() < deadline:
        if not parent_process_alive(pid):
            return
        time.sleep(0.025)

    if os.name != "nt" and parent_process_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _scrub_shadow_pids(shadow_pids: set[int]) -> tuple[int, ...]:
    """Remove only residual workers whose PIDs were reported by this run."""
    residual_before = sorted(pid for pid in shadow_pids if parent_process_alive(pid))
    for pid in residual_before:
        _terminate_pid_tree(pid)

    residual_after = tuple(
        sorted(pid for pid in shadow_pids if parent_process_alive(pid))
    )
    if shadow_pids:
        if residual_after:
            v11._log(
                "Process hygiene: FAIL residual shadow PIDs="
                + ",".join(str(pid) for pid in residual_after)
            )
        else:
            v11._log(
                f"Process hygiene: clean | tracked={len(shadow_pids)} "
                f"forced={len(residual_before)} residual=0"
            )
    return residual_after


def authority_main(args) -> int:
    # V20 owns the actual authority semantics and process-isolated worker setup.
    return v20.authority_main(args)


def supervise_main() -> int:
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--authority-worker",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    shadow_pids: set[int] = set()
    result_code: int | None = None

    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            shadow_pids.update(_parse_shadow_pids(line))

            payload = line[line.find("PlannerV11:"):] if "PlannerV11:" in line else line
            if payload.strip().startswith("PlannerV11: FAIL watchdog stall"):
                code = 8
            else:
                code = v11._terminal_code(payload)
            if code is None:
                continue

            result_code = code
            try:
                proc.wait(timeout=v11._SUPERVISOR_GRACE_S)
            except subprocess.TimeoutExpired:
                v11._log(
                    "V21 supervisor: terminal result observed; terminating authority process tree"
                )
                v11._terminate_process_tree(proc)
            break

        if result_code is None:
            result_code = proc.wait()
        return int(result_code)
    except KeyboardInterrupt:
        v11._log(
            "V21 supervisor: Ctrl+C received; terminating authority + current shadow process tree"
        )
        v11._terminate_process_tree(proc)
        result_code = 130
        return result_code
    finally:
        # Do this even after a nominal authority exit.  The parent lease remains
        # the normal fallback; this supervisor fence makes the Task Manager
        # post-condition explicit: no PID from this run is allowed to survive.
        if proc.poll() is None:
            v11._terminate_process_tree(proc)
        _scrub_shadow_pids(shadow_pids)


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        # V20/V19 normally spawn V19 shadow-worker scripts directly, but keep
        # this path valid for manual/debug invocation.
        return v20.main()
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
