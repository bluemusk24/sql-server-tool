# Deployable observability backend (#48).
#
# Grafana's all-in-one grafana/otel-lgtm image — collector, Prometheus
# and Grafana (Pyroscope, Loki and Tempo dropped: the free instance's
# 512 MB budget could not sustain Tempo's WAL under the apps' constant
# health-check trace stream — it OOM-cycled every ~6-9 minutes) — with
# three changes for the $0 Render deployment:
#
#   1. Caddy in front on the single public port (Render exposes one
#      port per service):
#        /v1/*  → OTLP ingest (token-gated) → collector :4318
#        else   → Grafana :3000 (login required)
#      Prometheus is never exposed: only Grafana reaches it, from
#      inside the container.
#   2. Trimmed to fit the free instance's 512 MB: no Pyroscope, no
#      Loki, no Tempo (traces accepted by the collector and discarded),
#      no Grafana plugin downloads, no Tempo span-metrics generator,
#      and every Go component heap-capped via GOMEMLIMIT (run-all.sh /
#      sdip-start.sh) — the kernel OOM-killer still took the whole
#      stack down once Grafana's heap drifted past ~430 MB RSS with
#      the default GC. Metrics + alerting only.
#   3. The SDIP application-metrics dashboard (#47) provisioned.
#
# Built by .github/workflows/observability.yaml, which publishes an
# immutable :YYYYMMDD-HHMMSS-shortsha-obs tag plus the :obs channel to
# GHCR. Runtime configuration is env-only (see render.yaml): the admin
# password and the OTLP ingest token are dashboard-only secrets.

# Caddy is a single static binary; the ubi9-micro base has no package
# manager, no tar — fetch and unpack in a scratch stage, then copy the
# binary in. Bump the version deliberately, not automatically.
FROM redhat/ubi9:9.8 AS caddy-fetch
ARG CADDY_VERSION=2.10.2
ADD https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz /tmp/caddy.tar.gz
RUN tar -xzf /tmp/caddy.tar.gz -C /tmp caddy && chmod 755 /tmp/caddy

FROM grafana/otel-lgtm:0.36.0

USER root

COPY --from=caddy-fetch /tmp/caddy /usr/local/bin/caddy

# Upstream entrypoint (patched: no Pyroscope/Loki/Tempo) + front door
# + Grafana provisioning overrides. Paths mirror the upstream layout.
COPY deploy/otel-lgtm/run-all.sh                 /otel-lgtm/run-all.sh
COPY deploy/otel-lgtm/run-grafana.sh             /otel-lgtm/run-grafana.sh
COPY deploy/otel-lgtm/grafana-plugins.sh         /otel-lgtm/grafana-plugins.sh
COPY deploy/otel-lgtm/sdip-start.sh              /otel-lgtm/sdip-start.sh
COPY deploy/otel-lgtm/Caddyfile                  /otel-lgtm/Caddyfile
COPY deploy/otel-lgtm/otelcol-config.yaml        /otel-lgtm/otelcol-config.yaml
COPY deploy/otel-lgtm/grafana-datasources.yaml   /otel-lgtm/grafana/conf/provisioning/datasources/grafana-datasources.yaml
COPY deploy/otel-lgtm/grafana-dashboards.yaml    /otel-lgtm/grafana/conf/provisioning/dashboards/grafana-dashboards.yaml
COPY grafana/provisioning/alerting/sdip-prod-alerts.yaml   /otel-lgtm/grafana/conf/provisioning/alerting/sdip-prod-alerts.yaml
COPY grafana/dashboards/application-metrics.json /otel-lgtm/sdip-application-metrics.json
RUN chmod +x /otel-lgtm/run-all.sh /otel-lgtm/run-grafana.sh /otel-lgtm/grafana-plugins.sh /otel-lgtm/sdip-start.sh

# Render proxies its HTTPS endpoint to this port (web-service default).
EXPOSE 10000

CMD ["/otel-lgtm/sdip-start.sh"]
