# Concurrent receding-horizon planning for SMB1

## Why this exists

The V1-V10 checkpoint planners are deliberately synchronous: save authoritative state, stop authoritative progress, evaluate counterfactual rollouts, then commit the selected macro. That was useful for proving action/state semantics, but it is the wrong execution architecture for an observable real-time controller.

The authoritative Mario trajectory must continue while planning is running.

The control analogy is closer to a sampled-data estimator/controller loop than to stop-and-search planning:

```text
authoritative Mesen
  step / observe / publish continuously
          |
          +---- newest checkpoint ----> shadow planner process
          |                               restore snapshot
          |                               evaluate horizon
          |                               publish latest plan
          |
          +<--- latest fresh action ------+
```

Mesen remains machine authority. The planner process is a predictive consumer of snapshots only.

## Hard boundary

Do not run counterfactual save/load rollouts in the authoritative Mesen instance.

A planner rollout mutates emulator time and machine state. Therefore concurrency requires a second Mesen instance in another process. The authoritative process never restores planner states.

```text
AUTHORITATIVE PROCESS                 SHADOW PLANNER PROCESS

Mesen A                               Mesen B
  |                                     |
  | step frame                           | load newest checkpoint
  | observe RAM                          | rollout candidates
  | publish UI                           | rank horizon
  | save periodic snapshot ------------>| 
  |                                     |
  |<--------------- latest plan --------|
  | apply only if still fresh            |
```

Separate processes are intentional. Mesen's native core should not be assumed to support two independent emulator instances inside one Python process.

## Control loop

Use a small control quantum instead of committing a 30-frame macro as one blocking decision.

Initial target:

- authoritative native step: 60 Hz when the host can sustain it,
- UI publish: 30 Hz,
- control update quantum: 4 native frames (~15 Hz),
- shadow-plan snapshot cadence: 4 frames initially,
- plan horizon: existing coarse/precision/recovery candidates,
- plan freshness limit: 8 native frames by default.

At every authoritative frame:

1. apply the currently selected controller buttons,
2. advance exactly one Mesen frame,
3. read structured SMB1 state,
4. derive events,
5. publish authoritative visualization at the UI sampling rate,
6. never wait for counterfactual planning.

At every control quantum:

1. save a checkpoint tagged with native frame id,
2. atomically offer it as the newest planner snapshot,
3. consume the newest available plan,
4. reject a plan whose root frame is too old,
5. switch controller buttons only at the control boundary.

The planner is allowed to miss deadlines. The game is not.

## Latest-value semantics

This is not a FIFO planning queue. A late plan for an old state is less useful than a newer state that has not yet been planned.

Use latest-value / overwrite semantics:

```text
snapshot 100 -> planning...
snapshot 104 arrives
snapshot 108 arrives

planner finishes plan rooted at 100
current authoritative frame = 111

if age > freshness limit:
    discard plan(100)
    plan newest available snapshot(108)
```

Similarly, intermediate snapshots may be dropped. This is deliberate backpressure.

## Safety and authority

A shadow rollout may classify a candidate as a planning hazard (pit risk, terminal risk, no progress). That classification can influence action selection but does not create authoritative game events.

Authoritative `DIED` and `LEVEL_COMPLETED` remain derived from the real Mesen trajectory.

## Bootstrap behavior

Until the first fresh plan exists, keep a deterministic conservative bootstrap action. For the first implementation use `RIGHT+B` on open terrain because it is already present in the validated V7-V10 action vocabulary. Once the shadow planner publishes a fresh action, control transfers to the rolling planner.

Bootstrap is a temporary control action, not a planning result.

## Telemetry

The web UI should report both control and planning age:

```text
Authoritative frame : 812
Applied action      : RIGHT+A+B
Plan root frame     : 808
Plan age            : 4f
Planner state       : planning / ready / stale
```

This makes latency visible instead of hiding it behind playback buffering.

## Migration path

V10 remains a synchronous research oracle for validating candidate scoring and pit-risk semantics.

V11 introduces concurrent receding-horizon execution. It should reuse V10 candidate evaluation semantics in the shadow process but must not reuse V10's stop-authority-then-search control loop.

The target invariant is:

> Counterfactual computation may lag or be discarded; authoritative Mario execution never pauses waiting for it.
