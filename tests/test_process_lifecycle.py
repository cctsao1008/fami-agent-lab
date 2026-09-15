import os

from fami_pixel.runtime.process_lifecycle import (
    isolated_run_dir,
    parent_process_alive,
    windows_job_environment,
    windows_job_name_for_run,
)


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


def test_windows_job_name_is_deterministic_for_explicit_inputs():
    assert windows_job_name_for_run(pid=1234, stamp_ns=5678) == "Local\\FamiPixel-1234-5678"


def test_windows_job_environment_does_not_mutate_base():
    base = {"EXAMPLE": "1"}
    env = windows_job_environment("Local\\FamiPixel-test", base=base)

    assert base == {"EXAMPLE": "1"}
    assert env["EXAMPLE"] == "1"
    assert env["FAMI_PIXEL_WINDOWS_JOB_NAME"] == "Local\\FamiPixel-test"
