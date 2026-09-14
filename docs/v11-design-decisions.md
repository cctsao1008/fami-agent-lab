# V11 design decisions

## DD-1 — The plant never waits for planning

Authoritative Mesen advances one native frame at a time using the most recently accepted control schedule. Planning is asynchronous and may miss deadlines.

## DD-2 — Counterfactual execution is physically separated

Every shadow planner owns its own Mesen process and home directory. Save/load rollouts never execute in the authoritative Mesen instance.

## DD-3 — Latest-value semantics beat queue semantics

A newer snapshot supersedes old planning work. Workers abandon stale generations between rollouts. The authority applies only plans inside the configured freshness window.

## DD-4 — Candidate rollouts are parallelized

The coarse candidate family is sharded across independent shadow workers. Four workers are the initial default.

## DD-5 — Plans are schedules, not single buttons

A selected candidate returns its full sequence of button/duration segments. The authority indexes that schedule by native-frame age and applies the appropriate segment at each frame.

## DD-6 — Machine truth remains singular

Only authoritative Mesen can produce durable `DIED` / `LEVEL_COMPLETED` evidence. Shadow terminal classifications are planning-only.
