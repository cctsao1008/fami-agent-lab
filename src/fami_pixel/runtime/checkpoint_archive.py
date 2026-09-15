"""Preserve live Mesen checkpoints before the rolling planner deletes them.

The V11 authority intentionally keeps only a short rolling checkpoint window for
live planning.  Research scenarios need older states after a run completes, so
V22's supervisor runs this small sidecar watcher and copies stable ``live-*.mss``
files into ``<runtime>/archive/`` before the rolling window prunes them.

The archive is local runtime evidence under ``build/``; it is never committed.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import threading
import time


class LiveCheckpointArchive:
    """Background copier for stable live Mesen checkpoint files."""

    def __init__(self, runtime_dir: Path, *, poll_interval_s: float = 0.05) -> None:
        self.runtime_dir = Path(runtime_dir).expanduser().resolve()
        self.archive_dir = self.runtime_dir / "archive"
        self.poll_interval_s = float(poll_interval_s)
        if self.poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be > 0")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: dict[Path, tuple[int, int]] = {}
        self._archived: set[str] = set()

    @property
    def archived_count(self) -> int:
        return len(self._archived)

    def start(self) -> None:
        if self._thread is not None:
            return
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run,
            name="fami-pixel-checkpoint-archive",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(1.0, self.poll_interval_s * 4))
        # One final synchronous sweep catches the last checkpoint emitted just
        # before terminal evidence/process teardown.
        self.sweep(force=True)

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval_s):
            self.sweep(force=False)

    def sweep(self, *, force: bool = False) -> int:
        """Copy newly stable checkpoints and return the number copied this sweep."""
        copied = 0
        if not self.runtime_dir.is_dir():
            return copied
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        for source in sorted(self.runtime_dir.glob("live-*.mss")):
            if source.name in self._archived:
                continue
            try:
                stat = source.stat()
            except FileNotFoundError:
                continue
            if stat.st_size <= 0:
                continue

            signature = (int(stat.st_size), int(stat.st_mtime_ns))
            previous = self._seen.get(source)
            self._seen[source] = signature
            # During the background loop require the file to look unchanged on
            # two consecutive scans, avoiding a copy while Mesen is still writing.
            if not force and previous != signature:
                continue

            destination = self.archive_dir / source.name
            temp = self.archive_dir / f".{source.name}.tmp"
            try:
                shutil.copy2(source, temp)
                temp.replace(destination)
            except (FileNotFoundError, PermissionError, OSError):
                try:
                    temp.unlink()
                except OSError:
                    pass
                continue

            self._archived.add(source.name)
            copied += 1
        return copied


def checkpoint_generation(path: Path) -> int | None:
    """Parse ``live-000123.mss`` -> 123, returning None for other names."""
    name = Path(path).name
    if not (name.startswith("live-") and name.endswith(".mss")):
        return None
    digits = name[len("live-") : -len(".mss")]
    if not digits.isdigit():
        return None
    return int(digits)


def available_checkpoint_generations(runtime_dir: Path) -> tuple[int, ...]:
    """Return generations available either in the rolling window or archive."""
    runtime = Path(runtime_dir).expanduser().resolve()
    values: set[int] = set()
    for folder in (runtime, runtime / "archive"):
        if not folder.is_dir():
            continue
        for path in folder.glob("live-*.mss"):
            generation = checkpoint_generation(path)
            if generation is not None:
                values.add(generation)
    return tuple(sorted(values))


def resolve_checkpoint(runtime_dir: Path, generation: int) -> tuple[Path | None, int | None]:
    """Resolve the exact checkpoint, or nearest earlier preserved generation.

    Returning the nearest earlier state is useful if a watcher misses one file;
    callers can then align the timeline manifest to that actual generation.
    A later state is never substituted because that would move the scenario past
    the event the caller intended to study.
    """
    runtime = Path(runtime_dir).expanduser().resolve()
    generation = int(generation)
    for folder in (runtime, runtime / "archive"):
        exact = folder / f"live-{generation:06d}.mss"
        if exact.is_file():
            return exact, generation

    available = [g for g in available_checkpoint_generations(runtime) if g <= generation]
    if not available:
        return None, None
    actual = max(available)
    for folder in (runtime, runtime / "archive"):
        candidate = folder / f"live-{actual:06d}.mss"
        if candidate.is_file():
            return candidate, actual
    return None, None
