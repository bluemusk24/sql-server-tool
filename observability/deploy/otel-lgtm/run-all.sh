#!/usr/bin/env bash
#
# Patched copy of upstream grafana/docker-otel-lgtm v0.36.0
# docker/run-all.sh: Pyroscope, Loki AND Tempo removed (the deployed
# instance has 512 MB RAM for the whole stack, #48: Tempo's unbounded
# WAL under the apps' constant health-check trace stream OOM-cycled the
# instance every ~6-9 minutes). The collector accepts traces and
# discards them (file exporter -> /dev/null); metrics + alerting are
# unaffected. When rebasing onto a newer otel-lgtm, diff against
# upstream and carry these deletions over — everything else must stay
# identical.
#
# Additionally each component is started with a GOMEMLIMIT (see the
# memory budget note above the component list): Go's default GC lets
# the heap double before reclaiming, which a 512 MB instance cannot
# afford — after Tempo was removed Grafana alone still drifted to
# ~430 MB RSS and the kernel kept OOM-killing the stack. Carry the
# limits over on rebase too.

READY_FILE=${LGTM_READY_FILE:-/tmp/ready}
rm -f "$READY_FILE"

echo "Starting grafana/otel-lgtm ${LGTM_VERSION}"

# Stop any still-running backgrounded components, giving them time to stop
# cleanly before forcing any stragglers down. Used both for SIGTERM/SIGINT
# and when a component fails to start, so a startup failure doesn't leak
# orphaned processes.
stop_components() {
	local pids=()
	mapfile -t pids < <(jobs -pr)
	if ((${#pids[@]} == 0)); then
		return 0
	fi

	# The wrapper scripts exec the server processes, so these are the server
	# PIDs. Give them time to stop cleanly before forcing them down.
	kill -TERM "${pids[@]}" 2>/dev/null || true
	(
		sleep "${LGTM_SHUTDOWN_TIMEOUT_SECONDS:-5}"
		kill -KILL "${pids[@]}" 2>/dev/null || true
	) &
	local watchdog_pid=$!

	wait "${pids[@]}" 2>/dev/null || true
	kill "$watchdog_pid" 2>/dev/null || true
	wait "$watchdog_pid" 2>/dev/null || true
}

# Graceful shutdown: forward SIGTERM/SIGINT to all background jobs.
shutdown() {
	# Avoid re-entering the handler if another signal arrives while waiting.
	trap - SIGTERM SIGINT
	echo "Shutting down..."
	stop_components
	exit 0
}
trap shutdown SIGTERM SIGINT

# Record global start time
start_time_global=$(date +%s)

# Function to record the start time of a component
start_component() {
	local component=$1
	local start_time
	start_time=$(date +%s)
	# Store the start time in an associative array
	eval "start_time_${component}=${start_time}"
}

# Start all components and record their start times, keeping the PID of
# each so a component that exits before it becomes ready can be detected.
declare -A component_pids

# Memory budget (#48): 512 MB for every process in this container.
# All three components are Go programs — GOMEMLIMIT caps how far the
# heap may grow before the GC MUST reclaim, trading CPU for a bounded
# footprint instead of a kernel OOM kill of the entire stack. Evidence:
# after the Tempo removal the instance still OOM-killed ("server_failed
# / oomKilled 512Mi" Render events at 16:28, 17:43 and 17:55) once
# Grafana's heap drifted to ~300 MB of live objects (~430 MB RSS) within
# an hour — Go's default GC (GOGC=100) lets the heap double before
# reclaiming, which a 512 MB instance cannot afford. Heap budget:
# Grafana 180 + collector 110 + Prometheus 80 MiB, plus Caddy 16 MiB
# (sdip-start.sh) — ~386 MB of heap targets, leaving headroom for
# runtimes and non-heap allocations under the 512 MB cap. Carry these
# limits over on rebase too.

start_component "grafana"
GOMEMLIMIT=180MiB ./run-grafana.sh &
component_pids[grafana]=$!

start_component "otelcol"
GOMEMLIMIT=110MiB ./run-otelcol.sh &
component_pids[otelcol]=$!

start_component "prometheus"
GOMEMLIMIT=80MiB ./run-prometheus.sh &
component_pids[prometheus]=$!

echo "Waiting for the OpenTelemetry collector and the Grafana LGTM stack to start up..."

# Declare arrays to store service status and elapsed times
declare -A service_ready elapsed_times

# Define services and their health check URLs
declare -A services
services["grafana"]="http://127.0.0.1:3000/api/health"
services["prometheus"]="http://127.0.0.1:9090/api/v1/status/runtimeinfo"
services["otelcol"]="http://127.0.0.1:13133/ready"

# Initialize service_ready status to false for all services
for service in "${!services[@]}"; do
	service_ready[$service]=false
done

# Function to check if a service is ready
check_service_ready() {
	local service=$1
	local url=$2

	# Skip if service is already marked as ready
	if [[ ${service_ready[$service]} == true ]]; then
		return 0
	fi

	# Check if service is ready
	if [[ $(curl -o /dev/null -sg "${url}" -w "%{response_code}" 2>/dev/null) == "200" ]]; then
		# Calculate and display startup time
		end_time=$(date +%s)
		start_var="start_time_${service}"
		# shellcheck disable=SC1083,SC2086
		start_time=$(eval echo \${$start_var})
		elapsed=$((end_time - start_time))
		# Store the elapsed time in the array
		elapsed_times[$service]=$elapsed
		service_ready[$service]=true
		echo "${service^} is up and running. Startup time: ${elapsed} seconds"
		return 0
	fi

	return 1
}

# Function to check if a component's process is still among the running
# background jobs (as opposed to having already exited).
pid_is_running() {
	local target=$1
	local pid
	for pid in "${running_pids[@]}"; do
		[[ $pid == "${target}" ]] && return 0
	done
	return 1
}

# Wait for all services to be ready
all_ready=false
while [[ $all_ready == false ]]; do
	# Check each service
	for service in "${!services[@]}"; do
		check_service_ready "$service" "${services[$service]}"
	done

	# Fail fast if a component's process has already exited, rather than
	# waiting forever for a health check that will never succeed.
	mapfile -t running_pids < <(jobs -pr)
	for service in "${!services[@]}"; do
		if [[ ${service_ready[$service]} == false ]] && ! pid_is_running "${component_pids[$service]}"; then
			echo "Error: ${service^} exited before becoming ready." >&2
			echo "Re-run with ENABLE_LOGS_${service^^}=true (or ENABLE_LOGS_ALL=true) to see its output." >&2
			stop_components
			exit 1
		fi
	done

	# Check if all services are ready
	all_ready=true
	for service in "${!service_ready[@]}"; do
		if [[ ${service_ready[$service]} == false ]]; then
			all_ready=false
			break
		fi
	done

	# If not all ready, wait a second before trying again
	if [[ $all_ready == false ]]; then
		sleep 1
	fi
done

# Calculate total startup time
end_time_global=$(date +%s)
total_elapsed=$((end_time_global - start_time_global))

echo "Total startup time: ${total_elapsed} seconds"

# Print time summary
echo -e "\nStartup Time Summary:"
echo "Grafana: ${elapsed_times[grafana]} seconds"
echo "Prometheus: ${elapsed_times[prometheus]} seconds"
echo "OpenTelemetry collector: ${elapsed_times[otelcol]} seconds"
echo "Total: ${total_elapsed} seconds"

touch "$READY_FILE"
echo "The OpenTelemetry collector and the Grafana LGTM stack are up and running. (created $READY_FILE)"

echo ""
echo "Open ports:"
echo " - 4318: OpenTelemetry HTTP endpoint (OTLP)"
echo " - 3000: Grafana (front door on :10000, see Caddyfile)"

# Wait for signal; backgrounded sleep allows the trap to fire
sleep infinity &
wait $!
