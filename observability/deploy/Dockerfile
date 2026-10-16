# Deployable observability backend (#48).
#
# Grafana's all-in-one grafana/otel-lgtm image — collector, Prometheus,
# Loki, Tempo and Grafana, i.e. the same stack as the local compose
# project in observability/ — with three changes for the $0 Render
# deployment:
#
#   1. Caddy in front on the single public port (Render exposes one
#      port per service):
#        /v1/*  → OTLP ingest (token-gated) → collector :4318
#        else   → Grafana :3000 (login required)
#      Prometheus, Loki and Tempo are never exposed: only Grafana
#      reaches them, from inside the container.
#   2. Pyroscope removed — profiles are unused and the free instance
#      has 512 MB RAM for the whole stack.
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

# Upstream entrypoint (patched: no Pyroscope) + front door + Grafana
# provisioning overrides. Paths mirror the upstream image layout.
COPY deploy/otel-lgtm/run-all.sh                 /otel-lgtm/run-all.sh
COPY deploy/otel-lgtm/sdip-start.sh              /otel-lgtm/sdip-start.sh
COPY deploy/otel-lgtm/Caddyfile                  /otel-lgtm/Caddyfile
COPY deploy/otel-lgtm/grafana-datasources.yaml   /otel-lgtm/grafana/conf/provisioning/datasources/grafana-datasources.yaml
COPY deploy/otel-lgtm/grafana-dashboards.yaml    /otel-lgtm/grafana/conf/provisioning/dashboards/grafana-dashboards.yaml
COPY grafana/dashboards/application-metrics.json /otel-lgtm/sdip-application-metrics.json
RUN chmod +x /otel-lgtm/run-all.sh /otel-lgtm/sdip-start.sh

# Render proxies its HTTPS endpoint to this port (web-service default).
EXPOSE 10000

CMD ["/otel-lgtm/sdip-start.sh"]
