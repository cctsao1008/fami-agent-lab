# V11 first machine-validation runbook

From the repository root:

```powershell
python .\examples\mesen_smb_checkpoint_planner_v11.py `
  "D:\my-github\nek\roms\Super Mario Bros. (Japan, USA).nes" `
  --web-ui `
  --shadow-workers 4
```

Expected startup evidence:

```text
Web UI    : http://127.0.0.1:8765/
Planner V11: continuous authority + 4 parallel shadow workers; control=4f freshness=8f
```

Expected live evidence:

```text
control update: ... root=<frame> age=<0..8>f worker=<0..3> compute=<ms>
```

Acceptance focus for this first run:

1. Mario motion remains continuous while planning occurs.
2. Native frame keeps increasing in the Web UI.
3. Control updates arrive with finite plan age.
4. No multi-second freeze appears at planning boundaries.
5. Death/completion still comes from authoritative execution.

Do not use this first run to tune policy quality. The purpose is to validate the concurrent control architecture before resuming obstacle/gap policy work.
