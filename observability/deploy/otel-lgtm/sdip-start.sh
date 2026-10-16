#!/usr/bin/env bash
# Entry point of the deployed observability backend (#48).
#   1. Caddy: public port, path routing, OTLP token gate
#   2. upstream run-all.sh: collector, Grafana, Loki, Prometheus and
#      Tempo (it waits until every component reports ready)
set -eu

# An unset token must never open the ingest: with no value the gate
# matches nothing, so /v1/* falls through to Grafana (404) instead of
# reaching the collector.
export OTLP_INGEST_TOKEN="${OTLP_INGEST_TOKEN:-unconfigured}"

echo "Starting Caddy (OTLP token gate + Grafana front door)..."
caddy run --config /otel-lgtm/Caddyfile --adapter caddyfile &

exec /otel-lgtm/run-all.sh
