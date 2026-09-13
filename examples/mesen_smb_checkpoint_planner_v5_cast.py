#!/usr/bin/env python3
"""Add sports-style narration and binary checkpoint capture to V5 planner output.

This is a presentation/capture layer only. It does not change planner decisions,
emulator state, checkpoint scoring, or episode semantics. The underlying V5
output is passed through verbatim while short narrative lines are inserted. At
each real decision boundary, the authoritative V5 root save-state is copied to a
stable capture directory so the human-readable log and binary machine state stay
paired for later analysis.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


DECISION_RE = re.compile(r"Decision #(\d+) \| frame (\d+) \| Mario X=(\d+)")
COMMITTED_RE = re.compile(
    r"Committed state: frame=(\d+) \| X=(\d+), Y=(\d+), "
    r"player_state=(\d+), horizontal_speed=([+-]?\d+), "
    r"vertical_speed=([+-]?\d+), engine=(0x[0-9A-Fa-f]+)"
)

DEFAULT_STATE_FILE = Path("build/checkpoints/smb1-planner-v5.mss")
DEFAULT_CAPTURE_DIR = Path("build/checkpoints/v5-cast-captures")


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


def initialize_capture_dir(capture_dir: Path) -> Path:
    capture_dir = capture_dir.expanduser().resolve()
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "index.tsv").write_text(
        "decision\tframe\tmario_x\tcheckpoint\n",
        encoding="utf-8",
    )
    return capture_dir


def capture_checkpoint(
    state_file: Path,
    capture_dir: Path,
    decision: int,
    frame: int,
    mario_x: int,
) -> Path | None:
    source = state_file.expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        return None

    destination = capture_dir / (
        f"decision-{decision:03d}-frame-{frame:06d}-X-{mario_x:04d}.mss"
    )
    shutil.copy2(source, destination)
    with (capture_dir / "index.tsv").open("a", encoding="utf-8", newline="") as index_file:
        index_file.write(
            f"{decision}\t{frame}\t{mario_x}\t{destination.name}\n"
        )
    return destination


def main() -> int:
    planner = Path(__file__).with_name("mesen_smb_checkpoint_planner_v5.py")
    raw_args = sys.argv[1:]
    state_file = extract_option(raw_args, "--state-file", DEFAULT_STATE_FILE)
    capture_dir = initialize_capture_dir(
        extract_option(raw_args, "--capture-dir", DEFAULT_CAPTURE_DIR)
    )
    planner_args = strip_wrapper_option(raw_args, "--capture-dir")
    command = [sys.executable, str(planner), *planner_args]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None

    decision_start_x: int | None = None
    current_decision: int | None = None
    beam_pick_pending = False

    print("\n=== Fami Pixel Sportscast ===", flush=True)
    print(
        "The planner is driving; this narrator only describes what the machine actually reports.",
        flush=True,
    )
    print(
        f"Binary capture : ON -> {capture_dir}",
        flush=True,
    )
    print(
        "Capture policy : one authoritative .mss snapshot at every real decision boundary\n",
        flush=True,
    )

    for line in process.stdout:
        print(line, end="")
        stripped = line.strip()

        match = DECISION_RE.search(stripped)
        if match:
            current_decision = int(match.group(1))
            frame = int(match.group(2))
            decision_start_x = int(match.group(3))
            captured = capture_checkpoint(
                state_file,
                capture_dir,
                current_decision,
                frame,
                decision_start_x,
            )
            print(
                f"PLAY-BY-PLAY : Decision {current_decision}. Mario sets up at X={decision_start_x} "
                f"on frame {frame}. The planner is reading the field.",
                flush=True,
            )
            if captured is not None:
                print(
                    f"BINARY SNAPSHOT: saved {captured.name} ({captured.stat().st_size} bytes)",
                    flush=True,
                )
            else:
                print(
                    f"BINARY SNAPSHOT: WARNING - checkpoint not available at {state_file}",
                    flush=True,
                )
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
            _frame = int(match.group(1))
            x = int(match.group(2))
            player_state = int(match.group(4))
            vy = int(match.group(6))
            engine = match.group(7)
            print(
                "PLAY-BY-PLAY : " + motion_call(decision_start_x, x, player_state, vy, engine),
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
    print(
        f"\nBinary archive : {capture_dir}",
        flush=True,
    )
    print(
        "To share the run, zip this directory; index.tsv maps each snapshot to decision/frame/X.",
        flush=True,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
