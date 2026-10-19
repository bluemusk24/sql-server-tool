# Observability stack (#45, #48)

Telemetry backend for the SQL Server Diagnostic Assistant, in two
flavours that share one purpose and one dashboard:

- **Local** — a **separate Docker Compose project**
  (`name: sdip-observability`) for development on this machine:
  own network, own lifecycle (`make observability` /
  `make observability-down`), never built or deployed by CI.
- **Deployed (#48)** — `deploy/` builds a Render service
  (`sdip-observability`, https://sdip-observability.onrender.com) from
  Grafana's all-in-one `grafana/otel-lgtm` image so **dev and prod can
  ship to it**. See [Deployed backend](#deployed-backend-on-render-48)
  below.

Both observe the product; neither is part of the application stack.

## Quickstart

```bash
make observability          # or: cd observability && docker compose up -d
make backend                # with OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
open http://localhost:3000  # Grafana — anonymous admin, no login
```

The backend's instrumentation (added in #44) is dormant until an OTLP
endpoint is set — pointing it at the collector's host port is the whole
activation. Stop with `make observability-down` (all storage is
container-local and ephemeral; a clean slate is `docker compose down -v`).

## The pieces

| Service | Port(s) | Role |
| --- | --- | --- |
| `otel-collector` | 4317 (OTLP gRPC), 4318 (OTLP HTTP), 8889 (its metrics), 13133 (health) | Receives OTLP from apps; fans out per signal |
| `tempo` | 3200 (query API) | Traces backend — receives from the collector over the Compose network |
| `loki` | 3100 | Logs backend — Loki 3.x ingests OTLP natively at `/otlp` |
| `prometheus` | 9090 | Metrics backend — scrapes the collector's `:8889` exporter |
| `grafana` | 3000 | Dashboards + Explore; all three datasources pre-provisioned |

Telemetry path:

```
app --OTLP--> collector --traces--> Tempo   (:3200)
                   |    --logs-----> Loki    (:3100)
                   +----metrics----> :8889 <-- Prometheus (:9090)
```

## Exploring

- **Dashboard**: home → **Application Metrics (#46)**
  (`/d/sdip-app-metrics`) — provisioned from
  `grafana/dashboards/application-metrics.json`. The **Environment** and
  **Deployed version** dropdowns filter every panel (multi-select, *All*
  by default) through the `deployment_environment` / `service_version`
  labels that the app attaches to every metric.
- **Traces**: Grafana → Explore → Tempo → Search → service `sql-server`
  — **local stack only**; the deployed backend is metrics + alerting
  only (Tempo removed for the 512 MB budget; traces are discarded at
  the collector).
  Every request is a trace; the root span is the HTTP request, children
  are the SQL queries (`db.statement`).
- **Metrics**: Explore → Prometheus → e.g. `otelcol_receiver_accepted_spans`
  or `otelcol_exporter_sent_spans` (collector health), labelled by pipeline.
  The app's own domain metrics (#46) land in the same place:
  `sdip_investigations_created_total`, `sdip_users_active`,
  `sdip_reports_generated_total`, `sdip_creations_failed_total` — all
  carrying `deployment_environment` and `service_version`.
- **Logs**: Explore → Loki → `{}` (nothing from the app yet — the backend
  doesn't ship logs over OTLP; adding the OTLP log exporter to the
  instrumentation is a natural next step).
- The collector's own console view: `docker compose -p sdip-observability logs -f otel-collector`.

## Deployed backend on Render (#48)

`deploy/` turns a metrics-only variant of the same stack into a
**separate Render free service**:

| Piece | Role |
| --- | --- |
| `deploy/Dockerfile` | `FROM grafana/otel-lgtm:0.36.0` + Caddy + our Grafana provisioning; publishes `ghcr.io/…/sql-server-tool:…-obs` and the `:obs` channel |
| `deploy/otel-lgtm/Caddyfile` | The one public port: `/v1/*` → OTLP ingest (token-gated), everything else → Grafana (login required). Prometheus stays on loopback |
| `deploy/otel-lgtm/sdip-start.sh` | Entrypoint: Caddy first, then upstream `run-all.sh` |
| `deploy/otel-lgtm/run-all.sh` | Upstream v0.36.0 copy **minus Pyroscope, Loki and Tempo** (512 MB RAM budget); rebase against upstream when bumping the base image |
| `deploy/otel-lgtm/grafana-*.yaml`, `grafana/dashboards/application-metrics.json`, `grafana/provisioning/alerting/sdip-alerts.yaml` | Datasources (uid `prometheus` etc.) + the SDIP dashboard + the alert rule, baked into the image so they survive data resets |

The deployed image is **metrics + alerting only, sized for the 512 MB
free instance**: Loki and Grafana's auto-installed plugin backends are
dropped, and **Tempo is removed entirely** — its WAL grew under the
apps' constant health-check trace stream and OOM-cycled the instance
every ~6-9 minutes (health checks failed during each reboot's ~74 s
Grafana start, producing Render "Instance failed" events). The
collector still *accepts* traces so client exporters never retry-storm,
but discards them (file exporter → `/dev/null`); dashboard and
alerting depend on Prometheus alone. The local compose stack keeps the
full Loki + Tempo for exploration.

Runtime configuration is env-only (see the `sdip-observability` block in
`render.yaml`); secrets are `sync: false`, dashboard-only:

| Variable | Purpose |
| --- | --- |
| `OTLP_INGEST_TOKEN` | Required header value `X-Sdip-Otlp-Token` on `/v1/*` — same token dev/prod send in `OTEL_EXPORTER_OTLP_HEADERS`. Unset ⇒ gate matches nothing ⇒ ingest fails closed |
| `GF_SECURITY_ADMIN_PASSWORD` | Grafana admin login (anonymous access and sign-ups are disabled) |
| `PROMETHEUS_EXTRA_ARGS` | TSDB size cap (`--storage.tsdb.retention.size=128MB`) — the disk is ephemeral |

CI: `.github/workflows/observability.yaml` builds and pushes on changes
to `observability/**`, then fires the service's deploy hook
(`RENDER_OBS_DEPLOY_HOOK` secret). Free-tier caveats: data resets on
redeploy/restart/spin-down; the stack sleeps ~15 min after the last
request; 750 free instance hours/month are shared with both app
services. Resource attributes arrive as Prometheus labels via the
image's `promote_resource_attributes` (`deployment_environment`,
`service_version`) — the same label names the local stack produces, so
the dashboard works on both unchanged.

## Alerting

**Repeated creation failures** (`sdip-creation-failures`) is a
Grafana-managed alert rule provisioned from
`grafana/provisioning/alerting/sdip-alerts.yaml` into the `SDIP` folder — it
fires on the counter behind the dashboard's *Creation failures* panel
(`sdip_creations_failed_total`, `object_type` = `investigation` |
`report`):

- **Query**: `sum by (service_name, deployment_environment,
  service_version, object_type) (increase(sdip_creations_failed_total[15m]))`
- **Threshold**: `> 3` failures in 15 minutes, sustained `for: 5m` —
  creations are human-paced and normally never fail, so several
  failures in a short window already means users are stuck; the 5-minute
  sustain filters one-off blips. No-data is OK (quiet periods must not
  alert); a query error alerts.
- **Every firing alert carries**: `service_name`,
  `deployment_environment`, and `service_version` (labels promoted from
  the OTLP resource attributes), plus `owner: dba-team`, `severity:
  warning`, and the `dashboard_url` annotation — edit the `owner` in
  `sdip-alerts.yaml` if the on-call DBA changes.
- **Where you see it**: Grafana → **Alerting → Alerts**, and the
  dashboard's **Active alerts** panel. To also *receive* alerts
  (email/Slack/Teams webhook), add a contact point under **Alerting →
  Contact points** — Grafana's default email contact point needs SMTP
  credentials, so push delivery is deliberately left as a one-step
  hookup with whatever channel you have.
- **Local check**: `make observability` → the rule is in the same
  folder; the local stack provisions it identically.
- **Reaction**: `on-call-engineer/poll.py` (repo root) polls this
  Alertmanager API every minute and starts a headless coding-agent
  session per firing alert — see `on-call-engineer/README.md`.

## Notes

- Images are pinned; configs are mounted read-only; no host bind mounts,
  so nothing writes into the repo.
- Trace↔log correlation is provisioned at the datasource level but needs
  trace ids in the app's log lines to become useful (local stack —
  traces are discarded on the deployed backend).
- Optional next steps (deliberately not included): bring Tempo back to
  the deployed backend only if the memory budget grows (a paid instance
  or a further component cut) — with its spanmetrics generator for
  service maps and RED metrics; the OTLP log exporter for app logs in
  Loki.
