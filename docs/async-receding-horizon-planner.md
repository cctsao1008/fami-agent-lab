# Concurrent receding-horizon planning for SMB1

## Why this exists

The V1-V10 checkpoint planners are deliberately synchronous: save authoritative state, stop authoritative progress, evaluate counterfactual rollouts, then commit the selected macro. That was useful for proving action/state semantics, but it is the wrong execution architecture for an observable real-time controller.

The authoritative Mario trajectory must continue while planning is running.

The control analogy is closer to a sampled-data estimator/controller loop than to stop-and-search planning:

```text
authoritative Mesen
  step / observe / publish continuously
          |
          +---- newest checkpoint ----> shadow planner pool
          |                               restore snapshot
          |                               evaluate short horizon
          |                               publish latest plan
          |
          +<--- latest fresh action ------+
```

Mesen remains machine authority. Shadow planners are predictive consumers of snapshots only.

## Hard boundary

Do not run counterfactual save/load rollouts in the authoritative Mesen instance.

A planner rollout mutates emulator time and machine state. Therefore concurrency requires separate Mesen instances in separate processes. The authoritative process never restores planner states.

## Real-time deadline implication

The first V11 machine run exposed a critical timing fact: a native 30-frame rollout is not a viable live-control primitive when shadow stepping is near real time. Even with four workers, sharding eight 30-frame candidates means each worker needs roughly two candidate horizons before returning a result. An 8-frame freshness window expires long before that result can be consumed.

Therefore V11 separates two roles:

```text
V10 synchronous oracle
  deeper / longer horizon
  useful for research and policy validation

V11 live controller
  short horizon
  strict freshness deadline
  latest-value semantics
```

The initial V11 live candidate set uses four 8-12 frame probes, one per worker:

```text
RIGHT+B 8f
RIGHT+A+B 8f
RIGHT+A+B 12f
RIGHT 8f
```

The objective is not yet globally optimal Mario play. The objective is to prove a correct continuous-control architecture whose planning latency is visible and bounded.

## Control loop

Initial target:

- authoritative native step: continuous,
- UI publish: 30 Hz,
- control update quantum: 4 native frames (~15 Hz),
- snapshot cadence: 4 frames,
- live plan horizon: 8-12 frames,
- plan freshness limit: 16 native frames by default,
- four shadow workers in parallel.

At every authoritative frame:

1. apply the currently selected controller buttons,
2. advance exactly one Mesen frame,
3. read structured SMB1 state,
4. derive events,
5. publish authoritative visualization at the UI sampling rate,
6. never wait for counterfactual planning.

At every control quantum:

1. consume the newest available fresh plan,
2. reject stale plans by root-frame age,
3. save a new authoritative checkpoint,
4. atomically publish that snapshot as the newest planner root,
5. continue execution immediately.

The planner is allowed to miss deadlines. The game is not.

## Latest-value semantics

This is not a FIFO planning queue. A late plan for an old state is less useful than a newer state that has not yet been planned.

Intermediate snapshots may be dropped. Shadow workers always converge toward the newest available generation.

## Bootstrap behavior

Until the first fresh plan exists, V11 uses a deterministic pulse-jump bootstrap:

```text
RIGHT+A+B 8f
RIGHT+B   8f
repeat
```

This avoids the first implementation's blind continuous `RIGHT+B`, which reached the first enemy before any fresh shadow result was available.

Bootstrap is not a planning result; it is only the live controller's startup fallback.

## Terminal lifecycle

The first machine run also exposed a teardown failure: `PlannerV11: FAIL death` was printed, but the process could remain alive while native/shadow cleanup was still blocked.

V11 now uses an outer supervisor process. The authority process owns Mesen A and the shadow workers. After any terminal `PlannerV11:` line, the supervisor gives cleanup a short grace period and then terminates the full authority + descendant process tree on Windows if necessary.

The observable contract is simple:

> Once a terminal planner result is printed, the shell prompt must return promptly.

## Safety and authority

A shadow rollout may classify a candidate as a planning hazard. That classification can influence action selection but does not create authoritative game events.

Authoritative `DIED` and `LEVEL_COMPLETED` remain derived from the real Mesen trajectory.

## Telemetry

The Web UI reports:

```text
Authoritative frame
Applied action
Plan root frame
Plan age
Plan compute time
Planner state
```

This makes timing defects visible instead of hiding them behind playback buffering.

## Target invariant

> Counterfactual computation may lag, be discarded, or be replaced by a newer prediction; authoritative Mario execution never pauses waiting for it.
