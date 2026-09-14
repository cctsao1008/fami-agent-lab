# SMB1 surrogate learning from Mesen rollouts

Issue: #22

## Purpose

The learned model is a **surrogate predictor**, not the authority and not an end-to-end controller.

```text
Mesen rollout teacher
      |
      v
(state, candidate) -> outcome dataset
      |
      v
small feed-forward model
      |
      v
cheap candidate ranking
      |
      v
Mesen validates top-K
```

Mesen remains the source of machine truth and authoritative `DIED` / `LEVEL_COMPLETED` events.

## Why a surrogate

The live V11/V12 controller proved continuous authoritative execution, but even short 8–12 frame shadow-Mesen probes cost on the order of 130–200 ms per candidate on the first validated Windows machine. A small model can evaluate many candidates far more cheaply, leaving emulator rollouts for verification of only the most promising actions.

## Phase-1 record schema

Each JSONL row is self-contained and versioned (`schema = 1`). It stores enough raw information to change feature normalization/model topology later without regenerating the teacher data.

```text
schema
source
generation / worker
candidate
  name
  horizon_frames
  schedule[] { buttons, frames }
start
  native_frame
  world / level
  x / y / y_high
  vx / vy              # signed values
  player_state
  engine
  joypad
end
  same structured fields
target
  delta_x / delta_y
  end_vx / end_vy
  max_x
  elapsed_frames
  terminal
  death
  level_complete
  reached_flagpole
  no_progress
  descending_low
```

`descending_low` is a planning-risk label, not an authoritative death event.

## Initial collector

The first collector is deliberately offline and synchronous:

```powershell
python .\examples\mesen_smb_rollout_dataset.py `
  "D:\my-github\nek\roms\Super Mario Bros. (Japan, USA).nes" `
  --candidate-set live `
  --roots 40
```

Default output:

```text
build/datasets/smb1-rollouts.jsonl
```

For each root checkpoint the collector evaluates every candidate against the exact same saved Mesen state, records every result, then commits the best non-terminal teacher candidate to reach the next root. This is intentionally slower than V12; it is a dataset-generation oracle, not the live controller.

Validate/summarize the data with:

```powershell
python .\tools\summarize_smb1_rollouts.py .\build\datasets\smb1-rollouts.jsonl
```

## Model direction

The first model should remain small and inspectable. `codeplea/genann` is the preferred initial runtime reference because it is C99, dependency-free, and implemented as one source/header pair. FANN is useful later as a richer training/runtime comparison. No ANN dependency belongs in the authoritative controller until measurements justify it.

A first MLP experiment can start around:

```text
input projection
  start x-local / y / vx / vy
  player_state / engine
  encoded candidate buttons
  candidate horizon
       |
       v
32 hidden
       |
       v
16 hidden
       |
       v
outputs
  delta_x
  end_y
  end_vy
  death-risk
  no-progress-risk
```

The exact feature normalization and output encoding are research artifacts and should be measured rather than assumed.

## Promotion gates

A learned predictor should not enter V12/V13 candidate selection until an offline report measures at least:

- `delta_x` MAE,
- end-Y/end-VY error,
- death-risk recall (false negatives matter most),
- no-progress/hazard recall,
- top-K candidate ranking agreement with Mesen,
- inference latency on the Windows development machine,
- robustness on roots not used for training.

The intended eventual use is **proposal + top-K validation**, not replacement of Mesen truth.
