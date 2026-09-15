import os

from fami_pixel.runtime.process_lifecycle import isolated_run_dir, parent_process_alive


def test_isolated_run_dir_is_unique(tmp_path):
    first = isolated_run_dir(tmp_path)
    second = isolated_run_dir(tmp_path)

    assert first != second
    assert first.parent == tmp_path.resolve()
    assert second.parent == tmp_path.resolve()
    assert first.is_dir()
    assert second.is_dir()
    assert first.name.startswith(f"run-{os.getpid()}-")


def test_parent_process_alive_for_current_process():
    assert parent_process_alive(os.getpid()) is True


def test_parent_process_alive_rejects_invalid_pid():
    assert parent_process_alive(0) is False
    assert parent_process_alive(-1) is False
