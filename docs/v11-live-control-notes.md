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

There were no `control update:` lines before death. That gave two pieces of evidence:

1. the authoritative loop really did continue running while the shadow processes were busy, so the architectural direction was correct,
2. the planner deadline model was wrong: four workers each evaluated two 30-frame candidates, so no plan arrived inside the 8-frame freshness window before the bootstrap `RIGHT+B` controller reached the first hazard.

A second bug was exposed at the terminal edge: after `PlannerV11: FAIL death`, the Python process did not return promptly. V11 now runs authority under an outer supervisor. Once a terminal `PlannerV11:` result is observed, the supervisor gives teardown a short grace period and then terminates the whole authority + shadow process tree on Windows if needed.

## Second machine run: fresh live plans validated

The revised 8–12 frame live pool produced continuous fresh control updates while the authoritative game kept advancing.

Representative evidence:

```text
control update: right+A+B 12f root=197 age=8f worker=2 compute=223.8ms
control update: right+B 8f root=201 age=8f worker=0 compute=136.1ms
control update: right+A+B 8f root=245 age=8f worker=1 compute=141.6ms
...
PlannerV11: FAIL death | frame=375 X=298
V11 supervisor: terminal result observed; terminating authority + shadow process tree
```

Observed live-planner characteristics:

- fresh plans usually arrived at age 8–12 frames,
- observed worker compute time was roughly 116–224 ms,
- plans remained inside the 16-frame freshness window,
- the shell returned promptly after the terminal event.

This is the first machine evidence that V11 is operating as a rolling sampled-data controller rather than the V1–V10 stop-plan-commit loop.

Policy quality is still immature: the live short-horizon controller died around X=298. That is now a policy/horizon problem, not evidence that authority is waiting for planning.

## Windows latest-value IPC race

Two additional runs exposed a separate implementation bug:

```text
PermissionError: [WinError 5] access denied
request.json.tmp -> request.json
```

The previous fixed-name atomic exchange used `os.replace()` while four shadow processes repeatedly opened `request.json`. Windows can briefly deny replacement while a reader has the destination open.

The request channel is **latest-value**, not FIFO. Therefore a transient publish conflict must not terminate or stall the authoritative plant.

The IPC writer now:

1. uses a unique temporary filename for each publish attempt,
2. retries `os.replace()` only for a few milliseconds on `PermissionError`,
3. drops that one publication if the sharing conflict persists,
4. lets the next generation supersede it.

An exhausted request publication is reported as:

```text
IPC backpressure: dropped planner snapshot generation=N after transient Windows sharing conflicts
```

Dropping an occasional planner snapshot is valid under the V11 contract; stopping Mario is not.

## Current acceptance focus

The concurrency contract now has machine evidence. The next run should verify that the Windows IPC fix prevents `PermissionError` from terminating authority. After that, the main research problem moves back to live policy quality and horizon design.

## Failure signals

The following indicate architecture problems rather than ordinary policy failure:

- the authoritative frame freezes while workers search,
- no `control update:` arrives before bootstrap reaches the first hazard,
- plan age repeatedly exceeds the freshness window,
- terminal output appears but the parent process does not return,
- a transient Windows IPC sharing conflict terminates authority,
- a shadow process touches the authoritative Mesen home or machine instance,
- an authoritative game event is inferred from a shadow candidate rather than real execution.

## Authority invariant

The authoritative process must never load a counterfactual state. Only shadow workers restore and roll out snapshots.

Authoritative `DIED` and `LEVEL_COMPLETED` remain derived from the real Mesen trajectory.
