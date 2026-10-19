#!/usr/bin/env bash
# Entry point of the deployed observability backend (#48).
#   1. Caddy: public port, path routing, OTLP token gate
#   2. upstream run-all.sh: collector, Grafana and Prometheus (Pyroscope,
#      Loki and Tempo are dropped for the 512 MB budget; it waits until
#      every component reports ready)
set -eu

# An unset token must never open the ingest: with no value the gate
# matches nothing, so /v1/* falls through to Grafana (404) instead of
# reaching the collector.
export OTLP_INGEST_TOKEN="${OTLP_INGEST_TOKEN:-unconfigured}"

echo "Starting Caddy (OTLP token gate + Grafana front door)..."
# Same 512 MB budget as run-all.sh: cap Caddy's Go heap too.
GOMEMLIMIT=16MiB caddy run --config /otel-lgtm/Caddyfile --adapter caddyfile &

exec /otel-lgtm/run-all.sh
