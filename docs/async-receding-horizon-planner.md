# Concurrent receding-horizon planning for SMB1

## Why this exists

The V1-V10 checkpoint planners are deliberately synchronous: save authoritative state, stop authoritative progress, evaluate counterfactual rollouts, then commit the selected macro. That was useful for proving action/state semantics, but it is the wrong execution architecture for an observable real-time controller.

The authoritative Mario trajectory must continue while planning is running.

The control analogy is closer to a sampled-data estimator/controller loop than to stop-and-search planning:

```text
authoritative Mesen
  step / observe / publish continuously
          |
          +---- newest checkpoint ----> parallel shadow planner pool
          |                               restore snapshot
          |                               evaluate candidate shards
          |                               publish latest plans
          |
          +<--- best fresh action --------+
```

Mesen remains machine authority. Shadow planners are predictive consumers of snapshots only.

## Hard boundary

Do not run counterfactual save/load rollouts in the authoritative Mesen instance.

A planner rollout mutates emulator time and machine state. Therefore concurrency requires separate shadow Mesen instances in separate processes. The authoritative process never restores planner states.

```text
AUTHORITATIVE PROCESS                   SHADOW PLANNER POOL

Mesen A                                 Mesen B0  shard 0
  |                                     Mesen B1  shard 1
  | step frame                           Mesen B2  shard 2
  | observe RAM                          Mesen B3  shard 3
  | publish UI                              |
  | save newest snapshot ------------------+--> parallel rollouts
  |                                            rank shard bests
  |<---------------- best fresh plan ---------+
  | apply only if still fresh
```

Separate processes are intentional. Mesen's native core should not be assumed to support multiple independent emulator instances inside one Python process.

## Control loop

Use a small control quantum instead of committing a 30-frame macro as one blocking decision.

Initial target:

- authoritative native step: 60 Hz when the host can sustain it,
- UI publish: 30 Hz,
- control update quantum: 4 native frames (~15 Hz),
- shadow-plan snapshot cadence: 4 frames initially,
- default shadow workers: 4,
- plan freshness limit: 8 native frames by default.

At every authoritative frame:

1. apply the currently selected controller buttons,
2. advance exactly one Mesen frame,
3. read structured SMB1 state,
4. derive events,
5. publish authoritative visualization at the UI sampling rate,
6. never wait for counterfactual planning.

At every control quantum:

1. consume the best fresh plan that is already available,
2. reject stale plans,
3. switch controller buttons only at the control boundary,
4. save a generation-numbered authoritative checkpoint,
5. atomically publish that snapshot as the newest planner request,
6. immediately return to authoritative stepping.

The planner is allowed to miss deadlines. The game is not.

## Parallel candidate evaluation

The V11 coarse candidate set is partitioned across shadow workers by worker index. With four workers and eight coarse candidates, each worker evaluates two candidates sequentially while all four workers run concurrently.

A worker checks for a newer planning generation between candidate rollouts. If a newer authoritative snapshot exists, it abandons the stale shard instead of finishing obsolete work.

The authority does not wait for all workers. At a control boundary it selects among the fresh worker responses that already exist. Selection prefers the newest root frame, then the candidate score.

This makes planning an asynchronous latest-value service rather than a barrier in the control loop.

## Generation-numbered checkpoints

Do not alternate only two checkpoint files. A lagging worker could still be reading one while the authority overwrites it for a newer generation.

V11 writes immutable generation-tagged snapshots such as:

```text
live-000001.mss
live-000002.mss
live-000003.mss
```

Old generations are deleted only after they are outside the freshness horizon plus a small safety margin.

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

Intermediate snapshots may be dropped. This is deliberate backpressure.

## Safety and authority

A shadow rollout may classify a candidate as a planning hazard (pit risk, terminal risk, no progress). That classification can influence action selection but does not create authoritative game events.

Authoritative `DIED` and `LEVEL_COMPLETED` remain derived from the real Mesen trajectory.

## Bootstrap behavior

Until the first fresh plan exists, keep a deterministic bootstrap action. The initial V11 implementation uses `RIGHT+B`, which is already in the validated V7-V10 action vocabulary. Once a fresh shadow plan exists, control transfers to rolling updates.

Bootstrap is a temporary control action, not a planning result.

## Telemetry

The Web UI reports both control state and planning age:

```text
Authoritative frame : 812
Applied action       : run_long_jump
Plan root frame      : 808
Plan age             : 4f
Plan compute         : 41.3 ms
Planner state        : parallel/4
```

This makes latency visible instead of hiding it behind playback buffering.

## Migration path

V10 remains a synchronous research oracle for validating candidate scoring and pit-risk semantics.

V11 reuses V10 rollout semantics in shadow processes but does not reuse V10's stop-authority-then-search control loop.

The target invariant is:

> Counterfactual computation may lag or be discarded; authoritative Mario execution never pauses waiting for it.

## Machine-validation acceptance

V11 is not considered complete until a real run proves:

- native frame IDs continue advancing while shadow workers compute,
- control updates show finite plan age,
- stale plans are rejected rather than applied,
- counterfactual `LoadState` happens only in shadow processes,
- Mario motion is continuous in the browser,
- authoritative `DIED` / `LEVEL_COMPLETED` semantics remain unchanged.
