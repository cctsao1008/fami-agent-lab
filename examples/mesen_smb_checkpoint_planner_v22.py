#!/usr/bin/env python3
"""V22 live planner: V20 gameplay inside a Windows kill-on-close Job Object.

Field evidence from V21 showed that the remaining post-run Python process was the
authority process itself, not a shadow worker. Its command line ended in
``--authority-worker``; the diagnostic script had misclassified it because the
substring ``--shadow-worker`` also matched the plural option
``--shadow-workers 4``.

V22 keeps V20 gameplay semantics and V21 PID hygiene, but adds an OS-owned final
lifecycle boundary on Windows:

- the supervisor creates a named Job Object with KILL_ON_JOB_CLOSE,
- the authority joins that job before it starts shadow workers,
- shadow workers inherit the authority's job membership,
- closing the supervisor's job handle kills any authority/shadow process that
  survives normal Python/native teardown.

This does not require administrator rights for the child processes created by
the current user. Mesen, radar, reward, landing-zone, watchdog, and learned-risk
semantics are unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from fami_pixel.runtime.process_lifecycle import (
    WindowsKillOnCloseJob,
    join_windows_job_from_env,
    windows_job_environment,
)

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v20 as v20
import mesen_smb_checkpoint_planner_v21 as v21


PLANNER_NAME = "v22-windows-job-lifecycle"


def authority_main(args) -> int:
    if os.name == "nt":
        try:
            if join_windows_job_from_env():
                v11._log("Process job : authority joined supervisor kill-on-close job")
            else:
                v11._log("Process job : no supervisor job supplied; using Python cleanup only")
        except OSError as exc:
            # Do not make gameplay availability depend on Job Object support.
            # V21/V20 cleanup remains the fallback and the failure is explicit.
            v11._log(f"Process job : join failed ({exc}); using fallback cleanup")
    return v20.authority_main(args)


def supervise_main() -> int:
    cmd = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        *sys.argv[1:],
        "--authority-worker",
    ]

    job = None
    env = os.environ.copy()
    if os.name == "nt":
        try:
            job = WindowsKillOnCloseJob.create()
            if job is not None:
                env = windows_job_environment(job.name, base=env)
                v11._log(f"Process job : {job.name} | KILL_ON_JOB_CLOSE")
        except OSError as exc:
            v11._log(f"Process job : creation failed ({exc}); using V21 fallback cleanup")
            job = None

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    assert proc.stdout is not None

    shadow_pids: set[int] = set()
    result_code: int | None = None

    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            shadow_pids.update(v21._parse_shadow_pids(line))

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
                    "V22 supervisor: terminal result observed; authority still alive after grace"
                )
            break

        if result_code is None:
            result_code = proc.wait()
        return int(result_code)
    except KeyboardInterrupt:
        v11._log("V22 supervisor: Ctrl+C received; closing current run process group")
        result_code = 130
        return result_code
    finally:
        # First close the OS ownership boundary. On Windows this is the strongest
        # post-condition: even an authority stuck in native teardown belongs to
        # the Job Object and must be terminated when this handle closes.
        if job is not None:
            v11._log("V22 supervisor: closing kill-on-close process job")
            job.close()

        # Keep the previous explicit cleanup as a portable belt-and-suspenders
        # fallback and as protection for an authority that failed to join the job.
        if proc.poll() is None:
            v11._terminate_process_tree(proc)
        v21._scrub_shadow_pids(shadow_pids)


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        return v20.main()
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
