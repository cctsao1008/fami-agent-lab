# V11 implementation checkpoint

Issue: #21 — Run authoritative SMB1 continuously while planning asynchronously.

Implemented on `main`:

- continuous authoritative frame stepping,
- separate shadow Mesen processes,
- four-way parallel candidate sharding by default,
- latest-value snapshot requests,
- generation-numbered immutable checkpoints,
- stale-plan rejection by native-frame age,
- rolling multi-command control schedules,
- V10 pit-aware rollout semantics in shadow workers,
- Web telemetry for plan root frame, plan age, compute time, and planner state,
- helper tests for candidate sharding, freshness selection, and schedule execution.

This checkpoint is source-complete but not machine-validated. The next acceptance step is one live V11 run showing native frames continuing to advance while shadow workers compute.
