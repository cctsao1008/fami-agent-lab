from pathlib import Path

from fami_pixel.runtime.checkpoint_archive import (
    LiveCheckpointArchive,
    available_checkpoint_generations,
    checkpoint_generation,
    resolve_checkpoint,
)


def test_checkpoint_generation_parser() -> None:
    assert checkpoint_generation(Path("live-000123.mss")) == 123
    assert checkpoint_generation(Path("request.json")) is None
    assert checkpoint_generation(Path("live-abc.mss")) is None


def test_archive_sweep_preserves_checkpoint(tmp_path: Path) -> None:
    runtime = tmp_path / "run-1"
    runtime.mkdir()
    source = runtime / "live-000007.mss"
    source.write_bytes(b"mesen-state")

    archive = LiveCheckpointArchive(runtime)
    assert archive.sweep(force=True) == 1
    preserved = runtime / "archive" / source.name
    assert preserved.read_bytes() == b"mesen-state"
    assert archive.archived_count == 1


def test_resolve_checkpoint_uses_archive_and_nearest_earlier(tmp_path: Path) -> None:
    runtime = tmp_path / "run-2"
    archive = runtime / "archive"
    archive.mkdir(parents=True)
    (archive / "live-000010.mss").write_bytes(b"ten")
    (archive / "live-000014.mss").write_bytes(b"fourteen")
    (runtime / "live-000020.mss").write_bytes(b"twenty")

    assert available_checkpoint_generations(runtime) == (10, 14, 20)

    exact, exact_generation = resolve_checkpoint(runtime, 14)
    assert exact_generation == 14
    assert exact is not None and exact.name == "live-000014.mss"

    earlier, earlier_generation = resolve_checkpoint(runtime, 18)
    assert earlier_generation == 14
    assert earlier is not None and earlier.name == "live-000014.mss"

    missing, missing_generation = resolve_checkpoint(runtime, 5)
    assert missing is None
    assert missing_generation is None
