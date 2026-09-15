from fami_pixel.learning.tiny_mlp import TinySurrogateMLP, evaluate_model, feature_vector


def _row(generation: int, candidate: str, delta_x: int, *, death=False, no_progress=False):
    buttons = 0x82 if candidate == "fast" else 0x80
    return {
        "schema": 1,
        "source": "offline-test",
        "generation": generation,
        "candidate": {
            "name": candidate,
            "horizon_frames": 8,
            "schedule": [{"buttons": buttons, "frames": 8}],
        },
        "start": {
            "x": 100 + generation * 4,
            "y": 176,
            "y_high": 1,
            "vx": 20,
            "vy": 0,
            "player_state": 0,
            "engine": 8,
            "joypad": 0,
        },
        "target": {
            "delta_x": delta_x,
            "death": death,
            "no_progress": no_progress,
        },
    }


def test_feature_vector_is_fixed_width_for_one_and_two_command_schedules():
    one = _row(0, "fast", 8)
    two = _row(1, "fast", 8)
    two["candidate"]["schedule"].append({"buttons": 0x80, "frames": 4})
    assert len(feature_vector(one)) == len(feature_vector(two))
    assert len(feature_vector(one)) > 20


def test_tiny_mlp_trains_and_reports_multitask_metrics():
    train = []
    for generation in range(12):
        train.append(_row(generation, "slow", 2, no_progress=(generation % 6 == 0)))
        train.append(_row(generation, "fast", 10, death=(generation >= 10)))

    model = TinySurrogateMLP(len(feature_vector(train[0])), hidden_size=8, seed=7)
    model.fit(train, epochs=40, learning_rate=0.02, seed=7)
    report = evaluate_model(model, train)

    assert report["records"] == len(train)
    assert report["delta_x_mae"] is not None
    assert report["ranking_groups"] == 12
    assert report["death"]["positives"] == 2
    assert report["no_progress"]["positives"] == 2
