from fami_pixel.games.smb1.watchdog import NoProgressWatchdog


def test_watchdog_triggers_one_soft_recovery_then_hard_abort():
    watchdog = NoProgressWatchdog(
        initial_frame=100,
        initial_x=200,
        min_progress_px=8,
        recover_after_frames=120,
        abort_after_frames=300,
    )

    assert watchdog.observe(219, 206).action == "ok"
    soft = watchdog.observe(220, 206)
    assert soft.action == "recover"
    assert soft.stagnant_frames == 120
    assert soft.recovery_count == 1

    # The same stall episode emits recovery only once.
    assert watchdog.observe(260, 206).action == "ok"

    hard = watchdog.observe(400, 206)
    assert hard.action == "abort"
    assert hard.stagnant_frames == 300
    assert hard.recovery_count == 1


def test_watchdog_meaningful_progress_resets_stall_episode():
    watchdog = NoProgressWatchdog(
        initial_frame=10,
        initial_x=100,
        min_progress_px=8,
        recover_after_frames=20,
        abort_after_frames=50,
    )

    assert watchdog.observe(30, 106).action == "recover"

    # Eight cumulative pixels beyond the anchor resets the timer and permits a
    # future recovery if a new stall develops.
    reset = watchdog.observe(31, 108)
    assert reset.action == "ok"
    assert reset.progress_anchor_x == 108
    assert reset.stagnant_frames == 0

    assert watchdog.observe(50, 108).action == "ok"
    second = watchdog.observe(51, 108)
    assert second.action == "recover"
    assert second.recovery_count == 2


def test_watchdog_rejects_invalid_thresholds_and_non_monotonic_frames():
    try:
        NoProgressWatchdog(initial_frame=0, initial_x=0, recover_after_frames=10, abort_after_frames=10)
    except ValueError as exc:
        assert "greater" in str(exc)
    else:
        raise AssertionError("expected threshold validation failure")

    watchdog = NoProgressWatchdog(initial_frame=10, initial_x=0)
    try:
        watchdog.observe(9, 0)
    except ValueError as exc:
        assert "monotonic" in str(exc)
    else:
        raise AssertionError("expected frame monotonicity failure")
