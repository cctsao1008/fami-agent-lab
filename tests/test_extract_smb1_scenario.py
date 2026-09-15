from pathlib import Path

from fami_pixel.runtime.checkpoint_archive import resolve_checkpoint


def test_historical_generation_is_unavailable_without_archive(tmp_path: Path) -> None:
    runtime = tmp_path / "run-old"
    runtime.mkdir()
    for generation in range(371, 381):
        (runtime / f"live-{generation:06d}.mss").write_bytes(b"state")

    path, generation = resolve_checkpoint(runtime, 185)
    assert path is None
    assert generation is None


def test_archived_historical_generation_is_resolved(tmp_path: Path) -> None:
    runtime = tmp_path / "run-new"
    archive = runtime / "archive"
    archive.mkdir(parents=True)
    (archive / "live-000185.mss").write_bytes(b"star-root")
    (runtime / "live-000371.mss").write_bytes(b"rolling")

    path, generation = resolve_checkpoint(runtime, 185)
    assert generation == 185
    assert path is not None
    assert path.parent == archive
