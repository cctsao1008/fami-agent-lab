#!/usr/bin/env python3
"""V18 live planner: V17 observability plus authoritative no-progress watchdog.

V17 can keep producing syntactically healthy plans while Mario is physically
stuck.  V18 treats that as a control livelock rather than a process hang:

- authoritative native frame + Mario world X drive a no-progress watchdog,
- a soft threshold injects one bounded back-off/re-arm recovery macro,
- a hard threshold converts persistent livelock into a watchdog_stall terminal
  result so V17's evidence recorder captures the scene automatically,
- Ctrl+C at the supervisor terminates the complete process tree without a noisy
  traceback.

The watchdog never uses learned scores to decide whether progress is real.
Mesen/SMB1 decoded state remains authoritative.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from fami_pixel.games.smb1 import (
    ActionCommand,
    GameEvent,
    GameEventType,
    PlanCandidate,
    Smb1Action,
)
from fami_pixel.games.smb1.watchdog import NoProgressWatchdog, WatchdogDecision

import mesen_smb_checkpoint_planner_v11 as v11
import mesen_smb_checkpoint_planner_v14 as v14
import mesen_smb_checkpoint_planner_v15 as v15
import mesen_smb_checkpoint_planner_v16 as v16
import mesen_smb_checkpoint_planner_v17 as v17


PLANNER_NAME = "v18-watchdog-live-radar"
WATCHDOG_MIN_PROGRESS_PX = 8
WATCHDOG_RECOVER_AFTER_FRAMES = 120
WATCHDOG_ABORT_AFTER_FRAMES = 300

WATCHDOG_RECOVERY = PlanCandidate(
    "watchdog_recovery",
    (
        ActionCommand(Smb1Action.LEFT, 12),
        ActionCommand(Smb1Action.NOOP, 2),
        ActionCommand(Smb1Action.RIGHT_B, 1),
        ActionCommand(Smb1Action.RIGHT_A_B, 11),
    ),
)

_watchdog: NoProgressWatchdog | None = None
_watchdog_decision: WatchdogDecision | None = None
_recovery_root_frame: int | None = None
_recovery_until_frame = -1
_watchdog_abort_requested = False

_original_derive_game_events = v17.derive_game_events
_original_best_plan = v16.best_coherent_live_radar_plan
_original_looks_grounded = v16._looks_grounded
_original_persist_terminal = v17._persist_terminal
_original_append_timeline = v17._append_timeline
_original_schedule_label = v14._schedule_label
_original_log = v11._log
_original_publish_core = v17.NesWebViewer.publish_core


def _reset_runtime_state() -> None:
    global _watchdog, _watchdog_decision
    global _recovery_root_frame, _recovery_until_frame, _watchdog_abort_requested
    _watchdog = None
    _watchdog_decision = None
    _recovery_root_frame = None
    _recovery_until_frame = -1
    _watchdog_abort_requested = False


def _recovery_active(frame: int) -> bool:
    return _recovery_root_frame is not None and int(frame) < _recovery_until_frame


def _watchdog_state(frame: int | None = None) -> str:
    if _watchdog_abort_requested:
        return "abort"
    if frame is not None and _recovery_active(frame):
        return "recovery"
    return "monitoring"


def _watchdog_derive_game_events(previous, current):
    """Observe authoritative progress and synthesize a terminal only on hard stall."""
    global _watchdog, _watchdog_decision
    global _recovery_root_frame, _recovery_until_frame, _watchdog_abort_requested

    events = _original_derive_game_events(previous, current)
    if any(e.kind in (GameEventType.DIED, GameEventType.LEVEL_COMPLETED) for e in events):
        return events

    if _watchdog is None:
        _watchdog = NoProgressWatchdog(
            initial_frame=int(previous.native_frame_id),
            initial_x=int(previous.mario_x_abs),
            min_progress_px=WATCHDOG_MIN_PROGRESS_PX,
            recover_after_frames=WATCHDOG_RECOVER_AFTER_FRAMES,
            abort_after_frames=WATCHDOG_ABORT_AFTER_FRAMES,
        )

    decision = _watchdog.observe(current.native_frame_id, current.mario_x_abs)
    _watchdog_decision = decision

    if decision.action == "recover":
        _recovery_root_frame = int(current.native_frame_id)
        _recovery_until_frame = _recovery_root_frame + WATCHDOG_RECOVERY.frame_count
        _original_log(
            "Watchdog: SOFT STALL -> bounded recovery | "
            f"frame={current.native_frame_id} X={current.mario_x_abs} "
            f"stagnant={decision.stagnant_frames}f anchorX={decision.progress_anchor_x} "
            f"recovery={decision.recovery_count}"
        )
    elif decision.action == "abort":
        _watchdog_abort_requested = True
        _original_log(
            "Watchdog: HARD STALL -> terminate with evidence | "
            f"frame={current.native_frame_id} X={current.mario_x_abs} "
            f"stagnant={decision.stagnant_frames}f anchorX={decision.progress_anchor_x}"
        )
        return events + (
            GameEvent(
                kind=GameEventType.DIED,
                frame_id=int(current.native_frame_id),
            ),
        )

    return events


def _watchdog_recovery_plan(current_frame: int, live_radar: dict) -> dict:
    assert _recovery_root_frame is not None
    return {
        "generation": -1,
        "worker": "watchdog",
        "root_frame": int(_recovery_root_frame),
        "candidate": WATCHDOG_RECOVERY.name,
        "schedule": v11._schedule_payload(WATCHDOG_RECOVERY),
        "score": [1, 0, 0, 0],
        "progress": 0,
        "terminal": "none",
        "age": int(current_frame) - int(_recovery_root_frame),
        "compute_ms": 0.0,
        "risk_probability": 0.0,
        "no_progress_probability": 1.0,
        "guard_mode": "watchdog-recovery",
        "live_radar": dict(live_radar or {}),
    }


def _watchdog_best_plan(
    response_paths: list[Path],
    current_frame: int,
    freshness: int,
    last_applied_generation: int,
    live_radar: dict,
):
    global _recovery_root_frame
    if _recovery_active(current_frame):
        return _watchdog_recovery_plan(current_frame, live_radar)
    if _recovery_root_frame is not None and int(current_frame) >= _recovery_until_frame:
        _recovery_root_frame = None
    return _original_best_plan(
        response_paths,
        current_frame,
        freshness,
        last_applied_generation,
        live_radar,
    )


def _watchdog_looks_grounded(observation) -> bool:
    # V17's emergency radar jump must not overwrite a bounded watchdog recovery.
    if _recovery_active(observation.native_frame_id):
        return False
    return _original_looks_grounded(observation)


def _watchdog_schedule_label(candidate_name: str) -> str:
    if candidate_name == WATCHDOG_RECOVERY.name:
        return "WATCHDOG: LEFT 12f -> RELEASE 2f -> RIGHT+B 1f -> RIGHT+A+B 11f"
    return _original_schedule_label(candidate_name)


def _watchdog_append_timeline(recorder, payload: dict) -> None:
    entry = dict(payload)
    decision = _watchdog_decision
    frame = int(entry.get("native_frame", -1))
    entry.update(
        {
            "watchdog_state": _watchdog_state(frame),
            "watchdog_stagnant_frames": None if decision is None else decision.stagnant_frames,
            "watchdog_progress_anchor_x": None if decision is None else decision.progress_anchor_x,
            "watchdog_max_x": None if decision is None else decision.max_x,
            "watchdog_recovery_count": None if decision is None else decision.recovery_count,
        }
    )
    _original_append_timeline(recorder, entry)


def _watchdog_persist_terminal(recorder, *, terminal: str, **kwargs) -> None:
    if _watchdog_abort_requested and terminal == "death":
        current = kwargs.get("current")
        _watchdog_append_timeline(
            recorder,
            {
                "event": "watchdog_abort",
                "generation": None,
                "native_frame": None if current is None else int(current.native_frame_id),
                "mario_x": None if current is None else int(current.mario_x_abs),
                "mario_y": None if current is None else int(current.mario_y),
                "action": kwargs.get("applied_label"),
                "guard_mode": "watchdog-hard-stall",
            },
        )
        terminal = "watchdog_stall"
    _original_persist_terminal(recorder, terminal=terminal, **kwargs)


def _watchdog_log(message: str) -> None:
    if _watchdog_abort_requested and message.startswith("PlannerV11: FAIL death"):
        message = message.replace("FAIL death", "FAIL watchdog stall", 1)
    _original_log(message)


def _watchdog_publish_core(self, core, observation, *, decision, mode, action, metadata=None):
    details = dict(metadata or {})
    wd = _watchdog_decision
    state = _watchdog_state(observation.native_frame_id)
    stagnant = None if wd is None else wd.stagnant_frames
    base_state = str(details.get("planner_state") or "live-radar")
    details["planner_state"] = (
        f"{base_state} | watchdog={state}"
        + ("" if stagnant is None else f"/{stagnant}f")
    )
    return _original_publish_core(
        self,
        core,
        observation,
        decision=decision,
        mode="LIVE RADAR + WATCHDOG",
        action=action,
        metadata=details,
    )


def _install_watchdog_overrides() -> None:
    _reset_runtime_state()
    v17.PLANNER_NAME = PLANNER_NAME
    v17.derive_game_events = _watchdog_derive_game_events
    v16.best_coherent_live_radar_plan = _watchdog_best_plan
    v16._looks_grounded = _watchdog_looks_grounded
    v17._append_timeline = _watchdog_append_timeline
    v17._persist_terminal = _watchdog_persist_terminal
    v14._schedule_label = _watchdog_schedule_label
    v11._log = _watchdog_log
    v17.NesWebViewer.publish_core = _watchdog_publish_core


def authority_main(args) -> int:
    _install_watchdog_overrides()
    _original_log(
        "Planner V18: authoritative no-progress watchdog enabled | "
        f"progress={WATCHDOG_MIN_PROGRESS_PX}px "
        f"recover={WATCHDOG_RECOVER_AFTER_FRAMES}f "
        f"abort={WATCHDOG_ABORT_AFTER_FRAMES}f"
    )
    return v17.authority_main(args)


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

    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            payload = line[line.find("PlannerV11:"):] if "PlannerV11:" in line else line
            if payload.strip().startswith("PlannerV11: FAIL watchdog stall"):
                code = 8
            else:
                code = v11._terminal_code(payload)
            if code is None:
                continue
            try:
                proc.wait(timeout=v11._SUPERVISOR_GRACE_S)
            except subprocess.TimeoutExpired:
                v11._log("V18 supervisor: terminal result observed; terminating process tree")
                v11._terminate_process_tree(proc)
            return code
        return proc.wait()
    except KeyboardInterrupt:
        v11._log("V18 supervisor: Ctrl+C received; terminating authority + shadow process tree")
        v11._terminate_process_tree(proc)
        return 130


def main() -> int:
    args = v11.parse_args()
    if args.shadow_worker:
        return v15.shadow_worker_main(args)
    if args.authority_worker:
        return authority_main(args)
    return supervise_main()


if __name__ == "__main__":
    raise SystemExit(main())
