# Live checkpoint archive for trajectory regression

The concurrent live planner deliberately keeps only a short rolling window of
`live-XXXXXX.mss` files so shadow workers do not accumulate transient state
forever.  That behavior is correct for live planning, but it is incompatible
with post-run deterministic scenario extraction: by the time a run ends, an
interesting Star, Mushroom, pit, or enemy-cluster checkpoint may already have
been pruned.

Current V22 supervisors therefore preserve stable live checkpoints under the
isolated runtime directory:

```text
build/checkpoints/v11-live/run-.../
  live-000371.mss            # rolling live window
  ...
  archive/
    live-000001.mss          # preserved research roots
    live-000002.mss
    ...
```

The archive is local runtime evidence under `build/` and remains gitignored.
Scenario extraction searches the rolling window first, then `archive/`, and may
fall back only to the nearest earlier preserved generation. It never silently
substitutes a later state because that could move the scenario past the event
being studied.

Historical runs that predate this archive cannot reconstruct checkpoints that
V11 already deleted. Their timeline remains useful as evidence, but a new live
run is required to capture deterministic replay roots.
