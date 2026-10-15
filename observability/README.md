# Observability stack (#45)

A **separate Docker Compose project** (`name: sdip-observability`) holding
the local, $0 telemetry backend for the SQL Server Diagnostic Assistant.
It observes the product; it is *not* part of the application stack — own
network, own lifecycle (`make observability` / `make observability-down`),
nothing here is touched by CI or deployed to Render (Render's free tier
cannot host always-on backends; the production candidate is Grafana Cloud
with the same OTLP configuration).

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

- **Traces**: Grafana → Explore → Tempo → Search → service `sql-server`.
  Every request is a trace; the root span is the HTTP request, children
  are the SQL queries (`db.statement`).
- **Metrics**: Explore → Prometheus → e.g. `otelcol_receiver_accepted_spans`
  or `otelcol_exporter_sent_spans` (collector health), labelled by pipeline.
- **Logs**: Explore → Loki → `{}` (nothing from the app yet — the backend
  doesn't ship logs over OTLP; adding the OTLP log exporter to the
  instrumentation is a natural next step).
- The collector's own console view: `docker compose -p sdip-observability logs -f otel-collector`.

## Notes

- Images are pinned; configs are mounted read-only; no host bind mounts,
  so nothing writes into the repo.
- Trace↔log correlation is provisioned at the datasource level but needs
  trace ids in the app's log lines to become useful.
- Optional next steps (deliberately not included): spanmetrics processor
  + Tempo metrics_generator for service maps and RED metrics; the OTLP
  log exporter for app logs in Loki.
