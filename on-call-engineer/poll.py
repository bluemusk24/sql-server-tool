#!/usr/bin/env python3
"""On-call engineer worker (#48 follow-up: alerting → reaction).

Polls the observability backend's alert API (Grafana Alertmanager on the
`sdip-observability` service) every minute. When an alert fires, it starts
one headless coding-agent session per alert (OpenCode by default) with the
alert details and an on-call prompt; the agent investigates, fixes a real
bug with the smallest correction + tests, or explains a false positive.

Stdlib only — no dependencies. Configuration is env-only; credentials are
never logged or persisted (session state holds alert fingerprints, not
secrets). Diagnostics stay read-only: the agent decides, the worker does
not remediate anything itself.

Environment:
  SDIP_OBS_URL          observability base URL (default https://sdip-observability.onrender.com)
  SDIP_OBS_USER         Grafana user (default admin)
  SDIP_OBS_PASSWORD     Grafana password — required (dashboard-only secret)
  ONCALL_FOLDER         Grafana folder to watch (default SDIP)
  ONCALL_POLL_SECONDS   poll interval (default 60)
  ONCALL_AGENT_CMD      headless agent command (default "opencode run --auto")
  ONCALL_AGENT_TIMEOUT  per-session timeout in seconds (default 1800)
  ONCALL_STATE_FILE     dedup state path (default .state.json next to this file)

Flags:
  --once      run a single polling pass and exit (for cron/CI wrappers)
  --dry-run   print what would be launched without launching it
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OBS_URL = "https://sdip-observability.onrender.com"
DEFAULT_AGENT_CMD = "opencode run --auto"
ONCALL_PROMPT = """You are the on-call engineer for this repository. An alert just fired.

Investigate the root cause. Read the code and reproduce the failure.
If you find a real bug, make the smallest correction, run the backend tests,
and commit the fix with a clear message.

If the alert is a false positive, explain why and do not change the code.

--- Alert details ---
 firing since : {starts_at}
 alert name   : {alertname}
 labels       : {labels}
 annotations  : {annotations}
 view in Grafana: Alerting -> Alerts on {obs_url} (folder {folder})
""".strip()


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {message}", flush=True)


def fetch_firing_alerts(base_url: str, user: str, password: str, folder: str) -> list[dict]:
    """Return firing (active) alerts from the Grafana Alertmanager API."""
    query = urllib.parse.urlencode({"filter": f'grafana_folder="{folder}"'})
    url = f"{base_url.rstrip('/')}/api/alertmanager/grafana/api/v2/alerts?{query}"
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    # 90s: the free-tier Grafana spins down and needs ~30-120s to wake.
    with urllib.request.urlopen(req, timeout=90) as resp:
        alerts = json.load(resp)
    return [a for a in alerts if a.get("status", {}).get("state") == "active"]


def build_prompt(alert: dict, base_url: str, folder: str) -> str:
    labels = alert.get("labels", {})
    return ONCALL_PROMPT.format(
        starts_at=alert.get("startsAt", "unknown"),
        alertname=labels.get("alertname", "unknown"),
        labels=json.dumps(labels, indent=None, sort_keys=True),
        annotations=json.dumps(alert.get("annotations", {}), indent=None, sort_keys=True),
        obs_url=base_url.rstrip("/"),
        folder=folder,
    )


def run_agent(command: str, prompt: str, cwd: Path, timeout: int, dry_run: bool) -> int:
    argv = shlex.split(command) + [prompt]
    if dry_run:
        log(f"dry-run: would launch: {argv[0]} … ({len(prompt)} char prompt)")
        log(f"dry-run: prompt:\n{prompt}")
        return 0
    log(f"launching agent session: {argv[0]} (timeout {timeout}s)")
    try:
        completed = subprocess.run(argv, cwd=cwd, timeout=timeout, check=False)
        return completed.returncode
    except subprocess.TimeoutExpired:
        log(f"agent session timed out after {timeout}s")
        return 124


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll observability alerts; start an agent session when one fires.")
    parser.add_argument("--once", action="store_true", help="single polling pass, then exit")
    parser.add_argument("--dry-run", action="store_true", help="print instead of launching the agent")
    args = parser.parse_args()

    base_url = os.environ.get("SDIP_OBS_URL", DEFAULT_OBS_URL)
    user = os.environ.get("SDIP_OBS_USER", "admin")
    password = os.environ.get("SDIP_OBS_PASSWORD", "")
    folder = os.environ.get("ONCALL_FOLDER", "SDIP")
    interval = int(os.environ.get("ONCALL_POLL_SECONDS", "60"))
    agent_cmd = os.environ.get("ONCALL_AGENT_CMD", DEFAULT_AGENT_CMD)
    agent_timeout = int(os.environ.get("ONCALL_AGENT_TIMEOUT", "1800"))
    state_path = Path(os.environ.get("ONCALL_STATE_FILE", Path(__file__).parent / ".state.json"))
    repo_root = Path(__file__).resolve().parent.parent

    if not password:
        log("SDIP_OBS_PASSWORD is required (Grafana admin password, dashboard-only secret)")
        return 2

    log(f"on-call worker watching {base_url} folder={folder} every {interval}s; agent={agent_cmd!r}")
    while True:
        try:
            alerts = fetch_firing_alerts(base_url, user, password, folder)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log(f"alert API unreachable: {exc}")
            alerts = None  # keep state; do not treat a network blip as "resolved"

        if alerts is not None:
            state = load_state(state_path)
            firing = {a["fingerprint"]: a for a in alerts if a.get("fingerprint")}
            for fingerprint, alert in firing.items():
                if fingerprint in state:
                    continue  # already handled while still firing
                name = alert.get("labels", {}).get("alertname", "unknown")
                env = alert.get("labels", {}).get("deployment_environment", "?")
                log(f"FIRING: {name} (environment={env}) — starting agent session")
                code = run_agent(agent_cmd, build_prompt(alert, base_url, folder), repo_root, agent_timeout, args.dry_run)
                state[fingerprint] = {
                    "alertname": name,
                    "environment": env,
                    "handled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "agent_exit_code": code,
                }
                log(f"agent session finished with exit code {code}; alert {fingerprint} marked handled")
            for fingerprint in set(state) - set(firing):
                log(f"alert {fingerprint} ({state[fingerprint].get('alertname')}) no longer firing — clearing")
                del state[fingerprint]
            if not args.dry_run:
                save_state(state_path, state)

        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
