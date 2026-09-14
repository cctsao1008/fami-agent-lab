# V11 acceptance checklist

- [ ] authoritative native frame advances continuously while shadow workers compute
- [ ] Web UI motion is continuous with no planning-boundary freeze
- [ ] plan root frame is visible
- [ ] plan age stays within configured freshness when a plan is applied
- [ ] stale plans are ignored
- [ ] shadow worker compute time is visible
- [ ] counterfactual load/rollout occurs only in shadow Mesen instances
- [ ] full multi-command schedules execute according to native-frame age
- [ ] authoritative `DIED` remains derived from the live Mesen trajectory
- [ ] authoritative `LEVEL_COMPLETED` remains derived from the live Mesen trajectory
