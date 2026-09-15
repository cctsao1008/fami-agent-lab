from fami_pixel.learning.tiny_mlp import TinySurrogateMLP, evaluate_model, feature_vector


def _row(
    generation: int,
    candidate: str,
    delta_x: int,
    *,
    death=False,
    doomed=False,
    no_progress=False,
):
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
            "doomed_within_probe": doomed,
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
        train.append(
            _row(
                generation,
                "slow",
                2,
                doomed=(generation == 9),
                no_progress=(generation % 6 == 0),
            )
        )
        train.append(_row(generation, "fast", 10, death=(generation >= 10)))

    model = TinySurrogateMLP(len(feature_vector(train[0])), hidden_size=8, seed=7)
    model.fit(train, epochs=40, learning_rate=0.02, seed=7)
    report = evaluate_model(model, train)

    assert report["records"] == len(train)
    assert report["delta_x_mae"] is not None
    assert report["ranking_groups"] == 12
    assert report["risk"]["positives"] == 3
    assert report["no_progress"]["positives"] == 2
    assert report["top2_oracle_coverage"] is not None
    assert report["top3_oracle_coverage"] is not None


def test_ranking_metrics_count_actual_delta_x_ties_as_correct():
    rows = [
        _row(0, "slow", 10),
        _row(0, "fast", 10),
    ]
    model = TinySurrogateMLP(len(feature_vector(rows[0])), hidden_size=4, seed=3)
    report = evaluate_model(model, rows)

    assert report["ranking_groups"] == 1
    assert report["top1_ranking_accuracy"] == 1.0
    assert report["top2_oracle_coverage"] == 1.0
    assert report["top3_oracle_coverage"] == 1.0


def test_tiny_mlp_json_round_trip_preserves_predictions(tmp_path):
    row = _row(3, "fast", 10, doomed=True)
    model = TinySurrogateMLP(len(feature_vector(row)), hidden_size=5, seed=11)
    before = model.predict(row)

    path = tmp_path / "model.json"
    model.save_json(path)
    restored = TinySurrogateMLP.load_json(path)
    after = restored.predict(row)

    assert restored.input_size == model.input_size
    assert restored.hidden_size == model.hidden_size
    assert after == before
