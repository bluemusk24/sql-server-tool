#!/usr/bin/env bash
# No-op replacement for upstream grafana/otel-lgtm v0.36.0
# grafana-plugins.sh (#48): the deployed free-tier instance (512 MB)
# must not delete/re-download the Drilldown apps on every cold start —
# that costs startup time, an external grafana.com dependency, and two
# plugin backend processes of RAM. To bring them back, restore the
# upstream file and re-test the memory budget.

refresh_stale_managed_plugins() {
	:
}
