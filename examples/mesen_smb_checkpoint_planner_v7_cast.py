#!/usr/bin/env python3
"""Sportscast + ZIP packaging wrapper for the V7 B-aware planner."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DECISION_RE = re.compile(r"Decision #(\d+) \| frame (\d+) \| Mario X=(\d+)")
COMMITTED_RE = re.compile(
    r"Committed state: frame=(\d+) \| X=(\d+), Y=(\d+), "
    r"player_state=(\d+), horizontal_speed=([+-]?\d+), "
    r"vertical_speed=([+-]?\d+), engine=(0x[0-9A-Fa-f]+)"
)
DEFAULT_CAPTURE_DIR = Path("build/checkpoints/v7-cast-captures")
DEFAULT_REPORT_DIR = Path("build/test-reports")


def extract_option(argv: list[str], name: str, default: Path) -> Path:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return Path(argv[i + 1])
        if arg.startswith(name + "="):
            return Path(arg.split("=", 1)[1])
    return default


def strip_wrapper_option(argv: list[str], name: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == name:
            i += 2
            continue
        if arg.startswith(name + "="):
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def action_call(label: str) -> str:
    low = label.lower()
    if "+a+b" in low:
        return "Mario accelerates into a running jump -- full speed and jump control together."
    if "+b" in low or "right+b" in low:
        return "Mario holds B and builds running speed."
    if "left" in low and "+a" in low:
        return "Mario turns back and jumps to reshape the approach."
    if "left" in low:
        return "Mario backs up to rebuild spacing and timing."
    if "noop" in low or "wait" in low:
        return "Mario releases the controls and lets the motion state settle."
    if "+a" in low or "jump" in low:
        return "Mario commits to a timed jump."
    if "right" in low:
        return "Mario keeps driving to the right."
    return f"Mario commits to {label}."


def motion_call(start_x: int | None, x: int, state: int, vy: int, engine: str) -> str:
    dx = 0 if start_x is None else x - start_x
    if dx > 0:
        text = f"Mario gains {dx} pixels and reaches X={x}."
    elif dx < 0:
        text = f"Mario gives back {-dx} pixels and moves to X={x}."
    else:
        text = f"No horizontal gain; Mario is at X={x}."
    if state == 0:
        text += " He is grounded."
    elif vy < 0:
        text += " He is rising."
    elif vy > 0:
        text += " He is descending."
    else:
        text += " He is airborne near the apex."
    if engine.lower() == "0x04":
        text += " Flagpole sequence detected."
    elif engine.lower() == "0x05":
        text += " Level-complete routine detected."
    return text


def main() -> int:
    planner = Path(__file__).with_name("mesen_smb_checkpoint_planner_v7.py")
    raw = sys.argv[1:]
    capture_dir = extract_option(raw, "--capture-dir", DEFAULT_CAPTURE_DIR).expanduser().resolve()
    report_dir = extract_option(raw, "--report-dir", DEFAULT_REPORT_DIR).expanduser().resolve()
    planner_args = strip_wrapper_option(raw, "--report-dir")
    command = [sys.executable, str(planner), *planner_args]

    report_dir.mkdir(parents=True, exist_ok=True)
    # Own a clean staging directory before worker launch. The worker will replace
    # it once planning starts; if preflight fails first, the wrapper can still
    # package planner.log/report.txt instead of throwing a second exception.
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)

    temp_log = report_dir / ".fami-pixel-v7-planner-current.log"
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None

    decision_x: int | None = None
    print("\n=== Fami Pixel V7 Sportscast ===", flush=True)
    print("B-aware running, jump-hold and airborne steering are all in the control space.\n", flush=True)

    with temp_log.open("w", encoding="utf-8", newline="") as log:
        for line in process.stdout:
            print(line, end="")
            log.write(line)
            log.flush()
            s = line.strip()
            m = DECISION_RE.search(s)
            if m:
                decision_x = int(m.group(3))
                print(f"PLAY-BY-PLAY : Decision {m.group(1)}. Mario is at X={decision_x} on frame {m.group(2)}.", flush=True)
                continue
            if s.startswith("CONTROL ALERT"):
                print("REPLAY BOOTH : Coarse control is collapsing; B-aware precision search is now active.", flush=True)
                continue
            if s.startswith("Selected action:"):
                print("CALL          : " + action_call(s.split(":", 1)[1].strip()), flush=True)
                continue
            m = COMMITTED_RE.search(s)
            if m:
                print("PLAY-BY-PLAY : " + motion_call(decision_x, int(m.group(2)), int(m.group(4)), int(m.group(6)), m.group(7)), flush=True)
                continue
            if "PlannerV7: FAIL death" in s:
                print("PLAY-BY-PLAY : The recovery window is gone -- this line ends in death.", flush=True)
            elif s == "=== LEVEL COMPLETE ===":
                print("PLAY-BY-PLAY : Flag down. World 1-1 is complete!", flush=True)

    code = process.wait()
    try:
        shutil.copy2(temp_log, capture_dir / "planner.log")
        index = capture_dir / "index.tsv"
        lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
        auth = sum(1 for line in lines[1:] if line.startswith("authoritative\t"))
        roots = sum(1 for line in lines[1:] if line.startswith("root-candidate\t"))
        report = [
            "Fami Pixel V7 B-aware Controller-Sequence Test Report",
            "====================================================",
            f"created_local: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"exit_code: {code}",
            f"authoritative_snapshot_count: {auth}",
            f"root_candidate_snapshot_count: {roots}",
            "planner: V7 B-aware controller-sequence planner",
            "control_model: horizontal intent x A state x B state x frame duration",
            "command:",
            "  " + subprocess.list2cmdline(command),
        ]
        (capture_dir / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive = Path(shutil.make_archive(str(report_dir / f"fami-pixel-v7-test-report-{stamp}"), "zip", root_dir=capture_dir.parent, base_dir=capture_dir.name))
        print("\n=== TEST REPORT READY ===", flush=True)
        print(f"Report ZIP      : {archive}", flush=True)
        if code != 0:
            print("Note            : worker failed before or during planning; ZIP still contains the complete failure log.", flush=True)
    finally:
        if temp_log.exists():
            temp_log.unlink()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
