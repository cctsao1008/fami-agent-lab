# Learned SMB1 surrogate model

## Goal

Build a compact learned transition/risk surrogate from authoritative Mesen rollout data.

The learned model is **not** machine authority and does not replace Mesen. Its job is to cheaply rank candidate actions so that the live controller can spend expensive Mesen shadow rollouts only on the most promising candidates.

```text
Authoritative Mesen
      |
      +--> state/action rollout samples --> dataset --> offline training
      |
      +--> live observation
                |
                v
        compact C inference model
                |
          rank candidate actions
                |
              top-K
                |
                v
        Mesen shadow validation
                |
                v
        authoritative control
```

## Why now

V11/V12 established concurrent receding-horizon execution, but native shadow rollouts still cost roughly O(100 ms) for short 8-12 frame probes on the validated Windows setup. That is sufficient for a small candidate set, but it does not scale to richer horizons or larger action vocabularies.

A learned surrogate can move most candidate screening out of the emulator.

## First model scope

Start with a small feed-forward MLP. The initial target is intentionally modest:

Inputs:

- Mario absolute X (or local normalized X)
- Mario Y
- horizontal velocity
- vertical velocity
- player state
- engine state
- candidate NES buttons
- candidate horizon in frames

Outputs:

- predicted delta-X
- predicted end-Y
- predicted end-VX
- predicted end-VY
- death probability / risk score
- no-forward-progress probability / risk score
- pit-risk probability / risk score

The first implementation should prefer a compact C runtime. `codeplea/genann` is the initial reference because it is C99, dependency-free, small, and easy to embed. FANN remains a useful benchmark and possible later backend, especially for fixed-point experiments.

## Authority boundary

Training labels come from Mesen rollouts. A model prediction never creates authoritative `DIED`, `LEVEL_COMPLETED`, or other game events.

Mesen remains:

- machine truth,
- rollout oracle,
- acceptance oracle for the learned model.

## Dataset contract

Each row describes one state/action rollout:

```text
sample_id
root_frame
root_x
root_y
root_vx
root_vy
root_player_state
root_engine
candidate_name
candidate_buttons
candidate_frames
end_frame
end_x
end_y
end_vx
end_vy
progress
max_x
terminal
reached_flagpole
death
pit_risk
no_progress
compute_ms
```

Dataset files are generated runtime artifacts and should live under `build/datasets/` by default, not be committed as source.

## Phase plan

### Phase A — data capture

Instrument live/shadow rollout paths to append structured oracle samples without changing controller authority or timing semantics.

Acceptance:

- samples are machine-derived,
- one row per completed candidate rollout,
- collection can be enabled/disabled explicitly,
- writer failure cannot stop authoritative Mario execution.

### Phase B — offline baseline

Train a compact MLP offline and report:

- delta-X MAE,
- end-Y MAE,
- end-VX/end-VY error,
- death-risk precision/recall,
- pit-risk recall,
- inference latency.

No live control integration yet.

### Phase C — top-K proposal model

Use the learned model to rank all cheap candidates, then validate only top-K with Mesen shadow instances.

Acceptance:

- authority remains continuous,
- Mesen remains final rollout validator,
- candidate coverage can grow without proportional native rollout cost,
- model inference latency is visible in telemetry.

## Non-goals for the first round

- end-to-end neural policy,
- replacing the SMB1 decoder,
- replacing Mesen physics,
- deep networks for their own sake,
- online weight updates inside the authoritative loop.
