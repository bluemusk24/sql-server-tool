#!/usr/bin/env bash
# Patched copy of upstream grafana/otel-lgtm v0.36.0
# docker/run-grafana.sh: the GF_PLUGINS_PREINSTALL block (grafana-llm-app,
# needed only for the Pyroscope drilldown) is removed — the free instance
# (512 MB, #48) must not download plugins nor spawn their backend
# processes on every cold start. Everything else stays identical.

# shellcheck disable=SC1091 # Flint 0.20.3 runs ShellCheck without source following.
source ./common.sh
source_sibling logging.sh
source_sibling grafana-plugins.sh

# Respect user-provided environment variables and apply defaults only if unset
export GF_AUTH_ANONYMOUS_ENABLED="${GF_AUTH_ANONYMOUS_ENABLED:-true}"

# Only set anonymous org role when anonymous auth is enabled
if [ "${GF_AUTH_ANONYMOUS_ENABLED}" != "false" ]; then
	export GF_AUTH_ANONYMOUS_ORG_ROLE="${GF_AUTH_ANONYMOUS_ORG_ROLE:-Admin}"
fi

export GF_PATHS_HOME=/data/grafana
export GF_PATHS_DATA=/data/grafana/data
export GF_PATHS_PLUGINS=/data/grafana/plugins

refresh_stale_managed_plugins

cd /otel-lgtm/grafana || exit 1
run_with_logging "Grafana ${GRAFANA_VERSION}" "${ENABLE_LOGS_GRAFANA:-false}" ./bin/grafana server
