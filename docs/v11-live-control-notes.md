# V11 live-control validation notes

V11 is the first fami-pixel planner whose authoritative SMB1 trajectory is not supposed to stop while counterfactual planning runs.

Validation should distinguish three clocks:

- `native frame`: authoritative Mesen execution,
- `plan root frame`: snapshot from which a shadow worker searched,
- `plan age`: `native_frame - plan_root_frame` when the action is consumed.

A valid live-control run should show native frames advancing steadily even while the planner pool is busy. A late result is discarded when `plan_age` exceeds the configured freshness window.

## Current defaults

```text
authority cadence : nominal 60 Hz
UI cadence        : 30 Hz
control quantum   : 4 frames (~15 Hz)
plan freshness    : 16 frames
shadow workers    : 4
live candidates   : 4 short probes, 8..12 frames each
```

The live pool is intentionally different from the V10 synchronous research oracle. V10 can afford 30-frame macros because it stops authority while searching. V11 cannot: if native shadow stepping is close to real time, a 30-frame rollout already consumes roughly half a second and is stale before a 4-frame control deadline.

The live candidate set is therefore:

```text
right_b_8
right_a_b_8
right_a_b_12
right_8
```

With four workers, each worker evaluates one live candidate. V10 pit-aware rollout semantics are still reused inside shadow instances.

Until the first fresh plan arrives, authority uses a repeating pulse-jump bootstrap:

```text
RIGHT+A+B 8f
RIGHT+B   8f
repeat
```

This is only a bootstrap controller; it is replaced as soon as a fresh shadow plan is available.

## First machine run: 2026-09-15

Observed:

```text
Planner V11: continuous authority + 4 parallel shadow workers; control=4f freshness=8f
PlannerV11: FAIL death | frame=329 X=314
```

There were no `control update:` lines before death. That gives two pieces of evidence:

1. the authoritative loop really did continue running while the shadow processes were busy, so the architectural direction is correct,
2. the planner deadline model was wrong: four workers each evaluated two 30-frame candidates, so no plan arrived inside the 8-frame freshness window before the bootstrap `RIGHT+B` controller reached the first hazard.

A second bug was exposed at the terminal edge: after `PlannerV11: FAIL death`, the Python process did not return promptly. V11 now runs authority under an outer supervisor. Once a terminal `PlannerV11:` result is observed, the supervisor gives teardown a short grace period and then terminates the whole authority + shadow process tree on Windows if needed.

## Changes after first machine run

- live candidate horizon reduced from 30f to 8..12f,
- one candidate per worker with four default workers,
- freshness window increased from 8f to 16f for this first real-time implementation,
- bootstrap changed from blind `RIGHT+B` to a repeating jump/run pulse,
- shadow stdout/stderr suppressed so native warnings do not interleave the authority console,
- terminal supervisor added so the shell returns even if native teardown blocks.

## Expected console evidence after the fix

A healthy run should contain live control updates before the first hazard, for example:

```text
[hh:mm:ss.mmm] Planner V11: continuous authority + 4 parallel shadow workers; control=4f freshness=16f live-horizon=8..12f
[hh:mm:ss.mmm] control update: right+B 8f root=... age=...f worker=... compute=...ms
[hh:mm:ss.mmm] control update: right+A+B 12f root=... age=...f worker=... compute=...ms
```

The important evidence is not whether World 1-1 is completed yet. It is:

1. native frames keep advancing while planning runs,
2. at least one fresh `control update:` arrives before bootstrap-only behavior reaches the first hazard,
3. `plan_age` stays within the freshness window when a plan is applied,
4. terminal output returns to the shell promptly.

## Failure signals

The following indicate architecture problems rather than ordinary policy failure:

- the authoritative frame freezes while workers search,
- no `control update:` arrives before bootstrap reaches the first hazard,
- plan age repeatedly exceeds the freshness window,
- terminal output appears but the parent process does not return,
- a shadow process touches the authoritative Mesen home or machine instance,
- an authoritative game event is inferred from a shadow candidate rather than real execution.

## Authority invariant

The authoritative process must never load a counterfactual state. Only shadow workers restore and roll out snapshots.
