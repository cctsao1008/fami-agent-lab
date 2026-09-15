# Hazard-focused SMB1 rollout collection

The first surrogate-learning dataset followed one greedy teacher trajectory per candidate family. That validated the Mesen rollout/export path and produced useful transition samples, but it yielded too few distinct hazard roots for death/no-progress classification.

The offline collector therefore supports an optional hazard-focused sampling mode. Mesen remains the only transition and terminal oracle; the sampling policy only decides which surviving counterfactual outcomes become future root states.

## Policy

For every root checkpoint, all candidates in the selected family are still evaluated from the exact same saved Mesen state and written to the JSONL dataset.

In `--sampling hazard` mode, terminal outcomes remain labelled samples but are never promoted to child roots. Among surviving outcomes, the collector promotes up to `--branch-width` children using three complementary priorities:

1. lowest progress, to seek stalled/regressive states and nearby hazards;
2. progress closest to zero, as a simple decision-boundary proxy;
3. highest progress/max-X, to preserve a viable forward trajectory.

The collector expands these children breadth-first and deduplicates identical structured SMB1 states. Hazard runs use a distinct source name such as `offline-precision-hazard`, so `(source, generation)` remains a valid root identity when appended to earlier greedy datasets.

## Example

```powershell
py .\examples\mesen_smb_rollout_dataset.py `
  "D:\my-github\nek\roms\Super Mario Bros. (Japan, USA).nes" `
  --candidate-set precision `
  --sampling hazard `
  --branch-width 3 `
  --roots 60 `
  --append
```

Then re-run the dataset summary and baseline split report. Before another ANN promotion attempt, the intended minimum coverage is at least 20 distinct death root groups and 20 distinct no-progress root groups, with multiple groups represented in train, validation, and test.

This mode is an offline data-acquisition strategy only. It does not alter V12 authoritative control, and learned predictions still cannot fabricate authoritative game events.
