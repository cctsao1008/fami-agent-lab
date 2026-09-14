# V11 live-control validation notes

V11 is the first fami-pixel planner whose authoritative SMB1 trajectory is not supposed to stop while counterfactual planning runs.

Validation should distinguish three clocks:

- `native frame`: authoritative Mesen execution,
- `plan root frame`: snapshot from which the shadow planner searched,
- `plan age`: `native_frame - plan_root_frame` when the action is consumed.

A valid live-control run should show native frames advancing steadily even while the planner is busy. A late result is discarded when `plan_age` exceeds the configured freshness window.

Initial defaults:

```text
authority cadence : nominal 60 Hz
UI cadence        : 30 Hz
control quantum   : 4 frames (~15 Hz)
plan freshness    : 8 frames
```

The initial shadow planner evaluates the bounded V7 coarse action family with V10 pit-aware rollout semantics. This is deliberately smaller than the synchronous V10 search stack; the first objective is proving the concurrency and freshness contract before restoring richer adaptive search.

The authoritative process must never load a counterfactual state. Only the shadow process restores and rolls out snapshots.
