"""Dependency-free one-hidden-layer baseline for SMB1 rollout surrogate learning.

Mesen remains the transition/terminal oracle.  This module provides an
inspectable Python reference model plus a small JSON persistence contract so a
trained surrogate can be exercised by live planner experiments without adding
an ANN runtime dependency yet.
"""

from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import random
from statistics import mean
from typing import Callable, Iterable


MAX_COMMANDS = 2
DELTA_X_SCALE = 80.0
MODEL_FORMAT = "fami-pixel-tiny-surrogate-v1"


def _button_bits(value: int) -> list[float]:
    value = int(value) & 0xFF
    return [1.0 if value & (1 << bit) else 0.0 for bit in range(8)]


def feature_vector(record: dict) -> list[float]:
    """Project a rollout record into a compact, runtime-friendly feature vector."""
    start = record["start"]
    candidate = record["candidate"]
    schedule = list(candidate.get("schedule", []))

    features = [
        float(start["x"]) / 4096.0,
        float(start["y"]) / 256.0,
        float(start.get("y_high", 0)) / 4.0,
        float(start["vx"]) / 64.0,
        float(start["vy"]) / 64.0,
        float(start.get("player_state", 0)) / 16.0,
        float(start.get("engine", 0)) / 32.0,
        float(candidate.get("horizon_frames", 0)) / 30.0,
    ]
    features.extend(_button_bits(start.get("joypad", 0)))

    for index in range(MAX_COMMANDS):
        if index < len(schedule):
            command = schedule[index]
            features.extend(_button_bits(command.get("buttons", 0)))
            features.append(float(command.get("frames", 0)) / 30.0)
        else:
            features.extend([0.0] * 8)
            features.append(0.0)
    return features


def _risk_label(record: dict) -> bool:
    target = record["target"]
    return bool(target.get("death")) or bool(target.get("doomed_within_probe"))


def target_vector(record: dict) -> tuple[float, float, float]:
    target = record["target"]
    return (
        float(target["delta_x"]) / DELTA_X_SCALE,
        1.0 if _risk_label(record) else 0.0,
        1.0 if target.get("no_progress") else 0.0,
    )


def _sigmoid(value: float) -> float:
    value = max(-40.0, min(40.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class TinySurrogateMLP:
    """One tanh hidden layer with delta-X, combined-risk, and no-progress heads."""

    def __init__(self, input_size: int, hidden_size: int = 16, *, seed: int = 22):
        self.input_size = int(input_size)
        self.hidden_size = int(hidden_size)
        rng = random.Random(seed)
        scale_in = 1.0 / math.sqrt(max(1, self.input_size))
        scale_hidden = 1.0 / math.sqrt(max(1, self.hidden_size))
        self.w1 = [
            [rng.uniform(-scale_in, scale_in) for _ in range(self.input_size)]
            for _ in range(self.hidden_size)
        ]
        self.b1 = [0.0] * self.hidden_size
        self.w2 = [
            [rng.uniform(-scale_hidden, scale_hidden) for _ in range(self.hidden_size)]
            for _ in range(3)
        ]
        self.b2 = [0.0, 0.0, 0.0]

    def _forward(self, x: list[float]) -> tuple[list[float], tuple[float, float, float]]:
        if len(x) != self.input_size:
            raise ValueError(f"expected {self.input_size} input features, got {len(x)}")
        hidden = []
        for row, bias in zip(self.w1, self.b1):
            activation = bias + sum(weight * value for weight, value in zip(row, x))
            hidden.append(math.tanh(activation))
        raw = [
            bias + sum(weight * value for weight, value in zip(row, hidden))
            for row, bias in zip(self.w2, self.b2)
        ]
        return hidden, (raw[0], _sigmoid(raw[1]), _sigmoid(raw[2]))

    def predict(self, record: dict) -> dict[str, float]:
        _, output = self._forward(feature_vector(record))
        return {
            "delta_x": output[0] * DELTA_X_SCALE,
            "risk_probability": output[1],
            "no_progress_probability": output[2],
        }

    def to_dict(self) -> dict:
        return {
            "format": MODEL_FORMAT,
            "input_size": self.input_size,
            "hidden_size": self.hidden_size,
            "outputs": ["delta_x", "risk_probability", "no_progress_probability"],
            "w1": self.w1,
            "b1": self.b1,
            "w2": self.w2,
            "b2": self.b2,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TinySurrogateMLP":
        if payload.get("format") != MODEL_FORMAT:
            raise ValueError(f"unsupported tiny surrogate format: {payload.get('format')!r}")
        input_size = int(payload["input_size"])
        hidden_size = int(payload["hidden_size"])
        model = cls(input_size, hidden_size, seed=0)
        w1 = [[float(value) for value in row] for row in payload["w1"]]
        b1 = [float(value) for value in payload["b1"]]
        w2 = [[float(value) for value in row] for row in payload["w2"]]
        b2 = [float(value) for value in payload["b2"]]
        if len(w1) != hidden_size or any(len(row) != input_size for row in w1):
            raise ValueError("invalid first-layer shape in tiny surrogate artifact")
        if len(b1) != hidden_size:
            raise ValueError("invalid first-layer bias shape in tiny surrogate artifact")
        if len(w2) != 3 or any(len(row) != hidden_size for row in w2):
            raise ValueError("invalid output-layer shape in tiny surrogate artifact")
        if len(b2) != 3:
            raise ValueError("invalid output-layer bias shape in tiny surrogate artifact")
        model.w1 = w1
        model.b1 = b1
        model.w2 = w2
        model.b2 = b2
        return model

    def save_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path) -> "TinySurrogateMLP":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    def fit(
        self,
        records: Iterable[dict],
        *,
        epochs: int = 800,
        learning_rate: float = 0.01,
        seed: int = 22,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        rows = list(records)
        if not rows:
            raise ValueError("cannot train TinySurrogateMLP on an empty dataset")

        risk_pos = sum(_risk_label(row) for row in rows)
        no_progress_pos = sum(bool(row["target"].get("no_progress")) for row in rows)
        risk_weight = min(20.0, max(1.0, (len(rows) - risk_pos) / max(1, risk_pos)))
        no_progress_weight = min(
            20.0,
            max(1.0, (len(rows) - no_progress_pos) / max(1, no_progress_pos)),
        )

        rng = random.Random(seed)
        order = list(range(len(rows)))
        total_epochs = int(epochs)
        for epoch_index in range(total_epochs):
            rng.shuffle(order)
            for row_index in order:
                row = rows[row_index]
                x = feature_vector(row)
                y_delta, y_risk, y_no_progress = target_vector(row)
                hidden, output = self._forward(x)
                p_delta, p_risk, p_no_progress = output

                out_grad = [
                    p_delta - y_delta,
                    0.5 * (risk_weight if y_risk else 1.0) * (p_risk - y_risk),
                    0.35
                    * (no_progress_weight if y_no_progress else 1.0)
                    * (p_no_progress - y_no_progress),
                ]

                hidden_grad = [0.0] * self.hidden_size
                for output_index in range(3):
                    grad = out_grad[output_index]
                    old_row = self.w2[output_index][:]
                    for hidden_index in range(self.hidden_size):
                        hidden_grad[hidden_index] += grad * old_row[hidden_index]
                        self.w2[output_index][hidden_index] -= learning_rate * grad * hidden[hidden_index]
                    self.b2[output_index] -= learning_rate * grad

                for hidden_index in range(self.hidden_size):
                    grad = hidden_grad[hidden_index] * (1.0 - hidden[hidden_index] ** 2)
                    for input_index in range(self.input_size):
                        self.w1[hidden_index][input_index] -= learning_rate * grad * x[input_index]
                    self.b1[hidden_index] -= learning_rate * grad

            if progress_callback is not None:
                progress_callback(epoch_index + 1, total_epochs)


def _classification_metrics(labels: list[bool], probabilities: list[float]) -> dict[str, float | int | None]:
    predicted = [probability >= 0.5 for probability in probabilities]
    tp = sum(p and y for p, y in zip(predicted, labels))
    fp = sum(p and not y for p, y in zip(predicted, labels))
    fn = sum((not p) and y for p, y in zip(predicted, labels))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "positives": sum(labels),
        "predicted_positives": sum(predicted),
        "precision": precision,
        "recall": recall,
    }


def _ranking_metrics(
    grouped: dict[tuple[str, int], list[tuple[dict, dict[str, float]]]]
) -> dict[str, float | int | None]:
    ranking_groups = 0
    top1_correct = 0
    top2_covered = 0
    top3_covered = 0

    for items in grouped.values():
        if len(items) < 2:
            continue
        ranking_groups += 1
        actual_max = max(float(item[0]["target"]["delta_x"]) for item in items)
        predicted_order = sorted(items, key=lambda item: item[1]["delta_x"], reverse=True)

        if float(predicted_order[0][0]["target"]["delta_x"]) == actual_max:
            top1_correct += 1
        if any(float(item[0]["target"]["delta_x"]) == actual_max for item in predicted_order[:2]):
            top2_covered += 1
        if any(float(item[0]["target"]["delta_x"]) == actual_max for item in predicted_order[:3]):
            top3_covered += 1

    return {
        "ranking_groups": ranking_groups,
        "top1_ranking_accuracy": top1_correct / ranking_groups if ranking_groups else None,
        "top2_oracle_coverage": top2_covered / ranking_groups if ranking_groups else None,
        "top3_oracle_coverage": top3_covered / ranking_groups if ranking_groups else None,
    }


def evaluate_model(model: TinySurrogateMLP, records: Iterable[dict]) -> dict:
    rows = list(records)
    predictions = [model.predict(row) for row in rows]
    errors = [
        abs(prediction["delta_x"] - float(row["target"]["delta_x"]))
        for prediction, row in zip(predictions, rows)
    ]

    grouped: dict[tuple[str, int], list[tuple[dict, dict[str, float]]]] = defaultdict(list)
    for row, prediction in zip(rows, predictions):
        grouped[(str(row.get("source", "")), int(row["generation"]))].append((row, prediction))

    return {
        "records": len(rows),
        "delta_x_mae": mean(errors) if errors else None,
        **_ranking_metrics(grouped),
        "risk": _classification_metrics(
            [_risk_label(row) for row in rows],
            [prediction["risk_probability"] for prediction in predictions],
        ),
        "no_progress": _classification_metrics(
            [bool(row["target"].get("no_progress")) for row in rows],
            [prediction["no_progress_probability"] for prediction in predictions],
        ),
    }
