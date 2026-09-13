#!/usr/bin/env python3
"""Add sports-style narration and test-report packaging to V5 planner output.

Binary checkpoint capture is owned by the V5 worker itself so snapshots are
atomic with the machine state they describe. This wrapper only narrates the
planner output, records the raw planner stream, and packages the worker-produced
capture directory into a shareable ZIP before exit.
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

DEFAULT_CAPTURE_DIR = Path("build/checkpoints/v5-cast-captures")
DEFAULT_REPORT_DIR = Path("build/test-reports")


def action_call(label: str) -> str:
    lower = label.lower()
    if "right only" in lower:
        return "Mario keeps the pressure on and charges to the right."
    if "short jump" in lower:
        return "Quick hop! Mario taps A and tries to clear the immediate danger."
    if "medium jump" in lower:
        return "Mario goes airborne with a medium jump -- a balanced attack for distance and control."
    if "long jump" in lower:
        return "Big leap! Mario holds A and commits to a long jump."
    return f"Mario commits to: {label}."


def motion_call(previous_x: int | None, x: int, player_state: int, vy: int, engine: str) -> str:
    parts: list[str] = []
    if previous_x is None:
        parts.append(f"Mario is now at X={x}.")
    else:
        dx = x - previous_x
        if dx > 0:
            parts.append(f"He gains {dx} pixels and reaches X={x}.")
        elif dx == 0:
            parts.append(f"No forward ground gained -- Mario is still at X={x}.")
        else:
            parts.append(f"He gives back {-dx} pixels and falls to X={x}.")

    if player_state == 0:
        parts.append("He is back on the ground.")
    elif vy < 0:
        parts.append("He is still rising through the air.")
    elif vy > 0:
        parts.append("He is coming down from the jump.")
    else:
        parts.append("He remains airborne with almost no vertical speed.")

    if engine.lower() == "0x04":
        parts.append("Flagpole sequence! The finish is in sight.")
    elif engine.lower() == "0x05":
        parts.append("That is the level-complete routine -- World 1-1 is done.")
    return " ".join(parts)


def extract_option(argv: list[str], name: str, default: Path) -> Path:
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return Path(argv[index + 1])
        prefix = name + "="
        if arg.startswith(prefix):
            return Path(arg[len(prefix):])
    return default


def strip_wrapper_option(argv: list[str], name: str) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == name:
            index += 2
            continue
        if arg.startswith(name + "="):
            index += 1
            continue
        result.append(arg)
        index += 1
    return result


def build_test_report_zip(
    capture_dir: Path,
    report_dir: Path,
    command: list[str],
    exit_code: int,
    raw_log_path: Path,
) -> Path:
    report_dir = report_dir.expanduser().resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    capture_dir = capture_dir.expanduser().resolve()
    capture_dir.mkdir(parents=True, exist_ok=True)

    if raw_log_path.is_file():
        shutil.copy2(raw_log_path, capture_dir / "planner.log")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_name = f"fami-pixel-v5-test-report-{timestamp}"
    snapshots = sorted(capture_dir.glob("*.mss"))
    authoritative = sorted(capture_dir.glob("*-authoritative-*.mss"))
    root_candidates = sorted(capture_dir.glob("*-root-*.mss"))
    report_text = [
        "Fami Pixel V5 Test Report",
        "========================",
        f"created_local: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"exit_code: {exit_code}",
        f"snapshot_count: {len(snapshots)}",
        f"authoritative_snapshot_count: {len(authoritative)}",
        f"root_candidate_snapshot_count: {len(root_candidates)}",
        f"capture_directory: {capture_dir}",
        "capture_authority: snapshots were produced synchronously inside the V5 worker",
        "command:",
        "  " + subprocess.list2cmdline(command),
        "",
        "Contents:",
        "  planner.log  - complete raw V5 planner stdout/stderr",
        "  index.tsv    - kind/decision/action/frame/X/size/SHA-256 to .mss mapping",
        "  *-authoritative-*.mss - real decision-boundary Mesen state",
        "  *-root-*.mss          - immediate counterfactual root-candidate endpoint",
        "",
        "Important:",
        "  authoritative snapshots belong to the committed episode timeline.",
        "  root-candidate snapshots are counterfactual planning evidence only.",
        "  SHA-256 values in index.tsv can be used to test whether apparently",
        "  identical observed states are also identical full Mesen save states.",
        "",
    ]
    (capture_dir / "report.txt").write_text("\n".join(report_text), encoding="utf-8")
    archive_base = report_dir / report_name
    archive = shutil.make_archive(
        str(archive_base),
        "zip",
        root_dir=capture_dir.parent,
        base_dir=capture_dir.name,
    )
    return Path(archive)


def main() -> int:
    planner = Path(__file__).with_name("mesen_smb_checkpoint_planner_v5.py")
    raw_args = sys.argv[1:]
    capture_dir = extract_option(raw_args, "--capture-dir", DEFAULT_CAPTURE_DIR).expanduser().resolve()
    report_dir = extract_option(raw_args, "--report-dir", DEFAULT_REPORT_DIR).expanduser().resolve()

    planner_args = strip_wrapper_option(raw_args, "--report-dir")
    planner_args = strip_wrapper_option(planner_args, "--capture-dir")
    planner_args.extend(["--capture-dir", str(capture_dir)])
    command = [sys.executable, str(planner), *planner_args]

    report_dir.mkdir(parents=True, exist_ok=True)
    raw_log_path = report_dir / ".fami-pixel-v5-planner-running.log"
    if raw_log_path.exists():
        raw_log_path.unlink()

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None

    decision_start_x: int | None = None
    beam_pick_pending = False

    print("\n=== Fami Pixel Sportscast ===", flush=True)
    print("The planner is driving; this narrator only describes what the machine actually reports.", flush=True)
    print(f"Binary capture : worker-owned -> {capture_dir}", flush=True)
    print("Capture policy : authoritative decision state + four immediate candidate endpoints", flush=True)
    print(f"Test report ZIP: automatic -> {report_dir}\n", flush=True)

    with raw_log_path.open("w", encoding="utf-8", newline="") as planner_log:
        for line in process.stdout:
            print(line, end="")
            planner_log.write(line)
            planner_log.flush()
            stripped = line.strip()

            match = DECISION_RE.search(stripped)
            if match:
                decision = int(match.group(1))
                frame = int(match.group(2))
                decision_start_x = int(match.group(3))
                print(
                    f"PLAY-BY-PLAY : Decision {decision}. Mario sets up at X={decision_start_x} "
                    f"on frame {frame}. The planner is reading the field.",
                    flush=True,
                )
                continue

            if stripped.startswith("Binary capture : authoritative decision"):
                print("DATA BOOTH    : Machine state locked in before the planner explores alternatives.", flush=True)
                continue

            if stripped.startswith("Safety audit:"):
                reason = stripped.removeprefix("Safety audit:").strip()
                print(
                    "REPLAY BOOTH : The quick read is not enough here. "
                    f"The planner is checking several possible futures ({reason}).",
                    flush=True,
                )
                continue

            if stripped == "Beam search choice:":
                beam_pick_pending = True
                continue

            if beam_pick_pending and stripped.startswith("first action:"):
                label = stripped.split(":", 1)[1].strip()
                print(
                    f"REPLAY BOOTH : After comparing the surviving futures, the booth recommends {label}.",
                    flush=True,
                )
                beam_pick_pending = False
                continue

            if stripped.startswith("Selected action:"):
                label = stripped.split(":", 1)[1].strip()
                print(f"CALL          : {action_call(label)}", flush=True)
                continue

            match = COMMITTED_RE.search(stripped)
            if match:
                x = int(match.group(2))
                player_state = int(match.group(4))
                vy = int(match.group(6))
                engine = match.group(7)
                print(
                    "PLAY-BY-PLAY : "
                    + motion_call(decision_start_x, x, player_state, vy, engine),
                    flush=True,
                )
                continue

            if "selected action led to DEATH" in stripped or "status=DEATH" in stripped:
                print(
                    "PLAY-BY-PLAY : Trouble! That line ends in a death state. The run is over.",
                    flush=True,
                )
                continue

            if stripped == "=== LEVEL COMPLETE ===":
                print(
                    "PLAY-BY-PLAY : The flag is down -- Mario has completed World 1-1!",
                    flush=True,
                )

    code = process.wait()
    try:
        report_zip = build_test_report_zip(
            capture_dir,
            report_dir,
            command,
            code,
            raw_log_path,
        )
        print("\n=== TEST REPORT READY ===", flush=True)
        print(f"Raw planner log : {capture_dir / 'planner.log'}", flush=True)
        print(f"Binary snapshots: {capture_dir}", flush=True)
        print(f"Report ZIP      : {report_zip}", flush=True)
        print(
            "Share the ZIP as-is; binary captures now come from the planner worker, not from stdout timing.",
            flush=True,
        )
    except Exception as exc:
        print(f"\nTEST REPORT WARNING: failed to create ZIP: {exc}", flush=True)
    finally:
        if raw_log_path.exists():
            raw_log_path.unlink()

    return code


if __name__ == "__main__":
    raise SystemExit(main())
