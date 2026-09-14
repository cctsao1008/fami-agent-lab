# V11 live-control validation notes

V11 is the first fami-pixel planner whose authoritative SMB1 trajectory is not supposed to stop while counterfactual planning runs.

Validation should distinguish three clocks:

- `native frame`: authoritative Mesen execution,
- `plan root frame`: snapshot from which a shadow worker searched,
- `plan age`: `native_frame - plan_root_frame` when the action is consumed.

A valid live-control run should show native frames advancing steadily even while the planner pool is busy. A late result is discarded when `plan_age` exceeds the configured freshness window.

Initial defaults:

```text
authority cadence : nominal 60 Hz
UI cadence        : 30 Hz
control quantum   : 4 frames (~15 Hz)
plan freshness    : 8 frames
shadow workers    : 4
coarse candidates : 8, sharded across workers
```

The shadow pool evaluates the bounded V7 coarse action family with V10 pit-aware rollout semantics. With four workers, each worker normally evaluates two candidates per generation. Workers abandon stale generations when a newer authoritative snapshot arrives.

The first objective is proving the concurrency and freshness contract before restoring richer adaptive beam search.

## Expected console evidence

A healthy run should contain interleaved live control updates such as:

```text
[hh:mm:ss.mmm] Planner V11: continuous authority + 4 parallel shadow workers; control=4f freshness=8f
[hh:mm:ss.mmm] control update: run root=244 age=4f worker=1 compute=38.2ms
[hh:mm:ss.mmm] control update: run_tap_jump root=252 age=4f worker=3 compute=41.7ms
```

The important observation is that `native frame` in the Web UI continues to change while shadow computation is in progress.

## Failure signals

The following indicate architecture problems rather than ordinary policy failure:

- the authoritative frame freezes while workers search,
- plan age repeatedly exceeds the freshness window,
- no shadow result arrives before bootstrap control reaches a hazard,
- a shadow process touches the authoritative Mesen home or machine instance,
- an authoritative game event is inferred from a shadow candidate rather than real execution.

## Authority invariant

The authoritative process must never load a counterfactual state. Only shadow workers restore and roll out snapshots.
