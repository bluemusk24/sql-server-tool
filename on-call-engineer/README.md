# On-call engineer worker

The last hop of the alerting pipeline: alerts fire, and *somebody* has to
react. Here, that somebody is a headless coding agent (OpenCode by
default) started by this worker.

```
observability backend  --(alert API, every 60s)-->  poll.py  --(session)-->  opencode run
        Grafana Alertmanager                     dedup by               investigates, fixes a
        (folder SDIP)                            fingerprint             real bug — or explains
                                                                        a false positive
```

`poll.py` (Python 3 stdlib only, no dependencies) polls the Grafana
Alertmanager API on the deployed observability backend for **firing**
alerts in the `SDIP` folder. Each new firing alert starts **one** agent
session carrying the user's on-call prompt plus the alert's labels and
annotations (service, environment, deployed version, owner, dashboard
URL). While the alert keeps firing, the fingerprint is remembered in
`.state.json` so no second session starts; when the alert resolves, the
fingerprint is cleared and a future re-fire triggers a fresh session.
Network blips keep the state — they are never treated as "resolved".

The worker never remediates anything itself (diagnostics are read-only);
it only *wakes the agent*, which follows the prompt: reproduce, smallest
fix + backend tests + commit if it's a real bug, explanation and no code
change if it's a false positive. Escalation to a human is deliberately
out of scope.

## Running

```bash
export SDIP_OBS_PASSWORD='<grafana admin password>'   # dashboard-only secret
python3 on-call-engineer/poll.py                      # loop forever, every 60s
python3 on-call-engineer/poll.py --once               # single pass (cron/CI-friendly)
python3 on-call-engineer/poll.py --dry-run            # show what would be launched
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `SDIP_OBS_URL` | `https://sdip-observability.onrender.com` | Observability backend |
| `SDIP_OBS_USER` | `admin` | Grafana user |
| `SDIP_OBS_PASSWORD` | — (required) | Grafana password |
| `ONCALL_FOLDER` | `SDIP` | Grafana folder to watch |
| `ONCALL_POLL_SECONDS` | `60` | Poll interval |
| `ONCALL_AGENT_CMD` | `opencode run --auto` | Headless agent command (any CLI works: `claude -p`, …) |
| `ONCALL_AGENT_TIMEOUT` | `1800` | Per-session timeout (seconds) |
| `ONCALL_STATE_FILE` | `.state.json` (git-ignored) | Dedup state |

Run it anywhere the repo is checked out and the agent CLI is installed —
your machine, or a CI scheduled workflow (GitHub Actions cron minimum is
5 minutes, not 1: `python3 on-call-engineer/poll.py --once` per tick).

## Notes

- Credentials are env-only, never logged or committed; the alert
  state file holds fingerprints and labels only.
- The alert itself is **not** a metric or a trace: it is Grafana's
  conclusion from metrics (threshold + duration) and carries the
  triggering context as labels/annotations — which is exactly what gets
  passed to the agent.
- Both dev and prod are covered by one worker: alerts arrive with
  `deployment_environment` labels (`dev` / `prod`).
