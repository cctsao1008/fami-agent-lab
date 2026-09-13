#!/usr/bin/env python3
"""Sportscast + test-report wrapper for the V6 adaptive SMB1 planner.

The V6 worker owns all binary capture. This wrapper only mirrors the planner log,
adds a concise play-by-play layer for headless runs, and packages the worker-owned
capture directory plus planner.log/report.txt into a shareable ZIP at exit.
"""

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
DEFAULT_CAPTURE_DIR = Path("build/checkpoints/v6-cast-captures")
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
    if "left+a" in low:
        return "Mario turns back and jumps -- a recovery move to change the approach."
    if "step left" in low:
        return "Mario backs up to reset his position and timing."
    if "wait" in low:
        return "Mario releases the controls and lets the physics settle."
    if "a only" in low:
        return "Mario jumps without adding forward input."
    if "right+a" in low or "jump" in low:
        return "Mario attacks the obstacle with a timed jump to the right."
    if "right" in low:
        return "Mario keeps moving right."
    return f"Mario commits to {label}."


def motion_call(start_x: int | None, x: int, player_state: int, vy: int, engine: str) -> str:
    parts: list[str] = []
    if start_x is not None:
        dx = x - start_x
        if dx > 0:
            parts.append(f"Mario gains {dx} pixels and reaches X={x}.")
        elif dx < 0:
            parts.append(f"Mario deliberately gives back {-dx} pixels and moves to X={x}.")
        else:
            parts.append(f"No horizontal gain; Mario remains at X={x}.")
    else:
        parts.append(f"Mario is at X={x}.")
    if player_state == 0:
        parts.append("He is grounded.")
    elif vy < 0:
        parts.append("He is rising.")
    elif vy > 0:
        parts.append("He is descending.")
    else:
        parts.append("He is airborne near the vertical apex.")
    if engine.lower() == "0x04":
        parts.append("Flagpole sequence detected.")
    elif engine.lower() == "0x05":
        parts.append("Level-complete routine detected.")
    return " ".join(parts)


def build_zip(capture_dir: Path, report_dir: Path, log_path: Path, command: list[str], code: int) -> Path:
    capture_dir = capture_dir.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    if not capture_dir.is_dir():
        raise FileNotFoundError(f"worker capture directory not found: {capture_dir}")
    shutil.copy2(log_path, capture_dir / "planner.log")
    index = capture_dir / "index.tsv"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    auth = sum(1 for line in lines[1:] if line.startswith("authoritative\t"))
    roots = sum(1 for line in lines[1:] if line.startswith("root-candidate\t"))
    report = [
        "Fami Pixel V6 Adaptive Control-Space Test Report",
        "================================================",
        f"created_local: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"exit_code: {code}",
        f"authoritative_snapshot_count: {auth}",
        f"root_candidate_snapshot_count: {roots}",
        "planner: V6 adaptive control-space planner",
        "capture_semantics: worker-owned atomic decision/root-candidate save states",
        "command:",
        "  " + subprocess.list2cmdline(command),
        "",
        "Key V6 behavior:",
        "  normal mode: coarse 30-frame macros",
        "  precision mode: short NOOP/LEFT/A/LEFT+A/RIGHT/RIGHT+A primitives",
        "  escalation: no-progress, state-divergent progress tie, or action convergence",
    ]
    (capture_dir / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = report_dir / f"fami-pixel-v6-test-report-{stamp}"
    return Path(shutil.make_archive(str(base), "zip", root_dir=capture_dir.parent, base_dir=capture_dir.name))


def main() -> int:
    planner = Path(__file__).with_name("mesen_smb_checkpoint_planner_v6.py")
    raw = sys.argv[1:]
    capture_dir = extract_option(raw, "--capture-dir", DEFAULT_CAPTURE_DIR)
    report_dir = extract_option(raw, "--report-dir", DEFAULT_REPORT_DIR)
    planner_args = strip_wrapper_option(raw, "--report-dir")
    command = [sys.executable, str(planner), *planner_args]

    report_dir.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    temp_log = report_dir.expanduser().resolve() / ".fami-pixel-v6-planner-current.log"
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None

    decision_x: int | None = None
    print("\n=== Fami Pixel V6 Sportscast ===", flush=True)
    print("The planner drives the emulator; commentary describes only reported machine evidence.\n", flush=True)

    with temp_log.open("w", encoding="utf-8", newline="") as log:
        for line in process.stdout:
            print(line, end="")
            log.write(line)
            log.flush()
            s = line.strip()
            m = DECISION_RE.search(s)
            if m:
                decision = int(m.group(1))
                frame = int(m.group(2))
                decision_x = int(m.group(3))
                print(f"PLAY-BY-PLAY : Decision {decision}. Mario is at X={decision_x} on frame {frame}.", flush=True)
                continue
            if s.startswith("CONTROL ALERT"):
                reason = s.split(":", 1)[1].strip()
                print(f"REPLAY BOOTH : The coarse control space is losing authority ({reason}). Precision mode is coming on.", flush=True)
                continue
            if s.startswith("Safety audit"):
                print("REPLAY BOOTH : Scheduled lookahead -- the planner checks the deeper field before committing.", flush=True)
                continue
            if s.startswith("Selected action:"):
                label = s.split(":", 1)[1].strip()
                print(f"CALL          : {action_call(label)}", flush=True)
                continue
            m = COMMITTED_RE.search(s)
            if m:
                x = int(m.group(2)); ps = int(m.group(4)); vy = int(m.group(6)); engine = m.group(7)
                print("PLAY-BY-PLAY : " + motion_call(decision_x, x, ps, vy, engine), flush=True)
                continue
            if "selected action led to DEATH" in s:
                print("PLAY-BY-PLAY : The recovery window is gone -- this line ends in death.", flush=True)
            elif s == "=== LEVEL COMPLETE ===":
                print("PLAY-BY-PLAY : Flag down. World 1-1 is complete!", flush=True)

    code = process.wait()
    try:
        archive = build_zip(capture_dir, report_dir, temp_log, command, code)
        print("\n=== TEST REPORT READY ===", flush=True)
        print(f"Worker captures : {capture_dir.expanduser().resolve()}", flush=True)
        print(f"Report ZIP      : {archive}", flush=True)
        print("Share the ZIP as-is; it contains log, metadata, index, and worker-owned .mss evidence.", flush=True)
    except Exception as exc:
        print(f"\nTEST REPORT WARNING: failed to create ZIP: {exc}", flush=True)
    finally:
        if temp_log.exists():
            temp_log.unlink()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
