"""MCP server (#51): bearer gate, tool surface, and REST parity.

The contract this file locks down:

- ``/mcp`` is bearer-guarded exactly like the REST API (401 without a
  valid opaque token).
- ``initialize``/``tools/list``/``tools/call`` work over one
  stateless JSON round trip each.
- Read tools return what their REST equivalents return (parity), and
  unknown ids surface as tool errors, not crashes.
- The human approval gate holds through MCP: runs are refused before
  approval, accepted after it.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from app.store import CATALOG

MCP = "/mcp"
_HTTP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_EXPECTED_TOOLS = {
    "list_data_sources",
    "list_investigations",
    "get_investigation",
    "get_report",
    "get_fleet_summary",
    "create_investigation",
    "start_investigation_run",
}


def _headers(token: str | None) -> dict[str, str]:
    headers = dict(_HTTP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _post(client: TestClient, payload: dict[str, Any], token: str | None):
    return client.post(MCP, json=payload, headers=_headers(token))


def _rpc(
    client: TestClient,
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    rpc_id: int = 1,
) -> dict[str, Any]:
    resp = _post(
        client,
        {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}},
        token,
    )
    assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
    return resp.json()


def _initialize(client: TestClient, token: str) -> dict[str, Any]:
    return _rpc(
        client,
        token,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "sdip-tests", "version": "0"},
        },
    )


def _call(
    client: TestClient, token: str, tool: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """tools/call and decode the result.

    Returns ``{"ok": bool, "data": Any, "error": str}`` — ``data`` is the
    decoded payload (structured content, else the JSON text content).
    """
    body = _rpc(
        client,
        token,
        "tools/call",
        {"name": tool, "arguments": arguments or {}},
        rpc_id=99,
    )
    assert "error" not in body, f"{tool}: {body['error']}"
    result = body["result"]
    if result.get("isError"):
        texts = [c.get("text", "") for c in result.get("content", [])]
        return {"ok": False, "data": None, "error": "\n".join(texts)}
    if "structuredContent" in result:
        payload = result["structuredContent"]
    else:
        texts = [
            c["text"] for c in result.get("content", []) if c.get("type") == "text"
        ]
        payload = json.loads("\n".join(texts)) if texts else None
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    return {"ok": True, "data": payload, "error": ""}


def _token(client: TestClient, auth_headers: dict[str, str]) -> str:
    return auth_headers["Authorization"].split(" ", 1)[1]


def _await_job(
    client: TestClient, auth_headers: dict[str, str], job_id: str, timeout: float = 20
) -> dict[str, Any]:
    """Poll the REST job endpoint until the MCP-scheduled run lands."""
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
        if job["status"] in ("succeeded", "failed", "canceled"):
            return job
        assert time.monotonic() < deadline, f"job stuck in {job['status']}"
        time.sleep(0.2)


# ── Auth gate ────────────────────────────────────────────────────────


def test_mcp_requires_a_valid_bearer_token(client: TestClient) -> None:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "sdip-tests", "version": "0"},
        },
    }
    anonymous = _post(client, init, None)
    assert anonymous.status_code == 401
    assert anonymous.headers.get("www-authenticate") == "Bearer"

    bogus = _post(client, init, "not-a-real-token")
    assert bogus.status_code == 401


def test_mcp_initialize_and_tool_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)

    # A plain POST must land without a redirect: MCP clients that don't
    # follow 307s would otherwise fail on the bare /mcp URL they
    # configure.
    direct = client.post(
        MCP,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "sdip-tests", "version": "0"},
            },
        },
        headers=_headers(token),
        follow_redirects=False,
    )
    assert direct.status_code == 200, direct.text

    init = _initialize(client, token)
    assert init["result"]["serverInfo"]["name"] == "sql-server-diagnostic-assistant"

    # The SDK answers a notification with 202; stateless mode needs no
    # session bookkeeping beyond it.
    noted = _post(
        client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        token,
    )
    assert noted.status_code in (200, 202)

    tools = _rpc(client, token, "tools/list", rpc_id=2)
    names = {t["name"] for t in tools["result"]["tools"]}
    assert names == _EXPECTED_TOOLS


# ── Read-tool parity with REST ───────────────────────────────────────


def test_mcp_read_tools_match_their_rest_equivalents(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)
    _initialize(client, token)

    catalog = _call(client, token, "list_data_sources")
    assert catalog["ok"] and len(catalog["data"]) == len(CATALOG)
    assert all(
        entry["min_permission"] for entry in catalog["data"]
    ), "every source declares what it needs"

    inv_list = _call(client, token, "list_investigations")
    rest_list = client.get("/investigations", headers=auth_headers).json()
    assert inv_list["ok"]
    assert [row["id"] for row in inv_list["data"]] == [
        row["id"] for row in rest_list
    ]

    inv_id = rest_list[0]["id"]
    detail = _call(client, token, "get_investigation", {"investigation_id": inv_id})
    rest_detail = client.get(f"/investigations/{inv_id}", headers=auth_headers).json()
    assert detail["ok"]
    for key in ("id", "status", "server_host", "server_port", "auth_type",
                "lookback_hours"):
        assert detail["data"][key] == rest_detail[key], key
    assert len(detail["data"]["catalog"]) == len(rest_detail["catalog"])

    report = _call(client, token, "get_report", {"investigation_id": inv_id})
    rest_report = client.get(f"/reports/{inv_id}", headers=auth_headers).json()
    assert report["ok"]
    assert report["data"] == rest_report

    fleet = _call(client, token, "get_fleet_summary")
    rest_fleet = client.get("/fleet", headers=auth_headers).json()
    assert fleet["ok"]
    # generated_at is stamped per call on both paths — compare the rest.
    fleet["data"].pop("generated_at", None)
    rest_fleet.pop("generated_at", None)
    assert fleet["data"] == rest_fleet


def test_mcp_unknown_ids_come_back_as_tool_errors(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)
    _initialize(client, token)

    missing_inv = _call(client, token, "get_investigation", {"investigation_id": 999_999})
    assert not missing_inv["ok"] and "not found" in missing_inv["error"].lower()

    # Seeded investigation #1 has a report; #2 (none exists) has neither.
    no_report = _call(client, token, "get_report", {"investigation_id": 999_999})
    assert not no_report["ok"] and "no report" in no_report["error"].lower()


# ── Create + run: validation and the human approval gate ─────────────


def test_mcp_create_investigation_validates_like_rest(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)
    _initialize(client, token)

    created = _call(
        client,
        token,
        "create_investigation",
        {
            "server_host": "sql-mcp-test.example",
            "auth_type": "ad",
            "lookback_hours": 4,
        },
    )
    assert created["ok"], created["error"]
    inv_id = created["data"]["id"]
    assert created["data"]["status"] == "draft"
    assert "A human must approve" in created["data"]["next_step"]

    # REST sees the same row (nothing hidden behind MCP).
    rest_detail = client.get(f"/investigations/{inv_id}", headers=auth_headers)
    assert rest_detail.status_code == 200
    assert rest_detail.json()["server_host"] == "sql-mcp-test.example"

    bad_host = _call(
        client, token, "create_investigation", {"server_host": "not a host!"}
    )
    assert not bad_host["ok"] and "valid hostname" in bad_host["error"]

    bad_lookback = _call(
        client,
        token,
        "create_investigation",
        {"server_host": "sql-ok.example", "lookback_hours": 3},
    )
    assert not bad_lookback["ok"]


def test_mcp_run_needs_human_approval_then_succeeds(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)
    _initialize(client, token)

    created = _call(
        client,
        token,
        "create_investigation",
        {"server_host": "sql-gate.example", "auth_type": "ad"},
    )
    assert created["ok"], created["error"]
    inv_id = created["data"]["id"]

    # Gate holds: no approvals recorded → the tool refuses, MCP has no
    # way to approve itself.
    blocked = _call(client, token, "start_investigation_run",
                    {"investigation_id": inv_id})
    assert not blocked["ok"]
    assert "approval gate" in blocked["error"].lower()

    # A human approves through the REST API (the UI's endpoint) …
    decisions = {entry.id: "approved" for entry in CATALOG}
    approved = client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions},
    )
    assert approved.status_code == 200, approved.text

    # … and only then the MCP run is accepted (demo mode, no credentials).
    started = _call(client, token, "start_investigation_run",
                    {"investigation_id": inv_id})
    assert started["ok"], started["error"]
    assert started["data"]["mode"] == "demo"

    job = _await_job(client, auth_headers, started["data"]["job_id"])
    assert job["status"] == "succeeded", job

    # The report the run produced is readable through MCP.
    report = _call(client, token, "get_report", {"investigation_id": inv_id})
    assert report["ok"], report["error"]


def test_mcp_refuses_to_restart_a_completed_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)
    _initialize(client, token)

    # Seeded investigation #1 is completed (and approved).
    started = _call(client, token, "start_investigation_run",
                    {"investigation_id": 1})
    assert not started["ok"]
    assert "run already started" in started["error"].lower()


def test_mcp_start_requires_full_sql_credentials(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = _token(client, auth_headers)
    _initialize(client, token)

    created = _call(
        client,
        token,
        "create_investigation",
        {"server_host": "sql-creds.example", "auth_type": "sql"},
    )
    assert created["ok"], created["error"]
    inv_id = created["data"]["id"]
    decisions = {entry.id: "approved" for entry in CATALOG}
    client.post(
        f"/investigations/{inv_id}/approvals",
        headers=auth_headers,
        json={"decisions": decisions},
    )

    # Password without username (or vice versa) is rejected, mirroring
    # the REST 422.
    partial = _call(
        client, token, "start_investigation_run",
        {"investigation_id": inv_id, "password": "pw-only"},
    )
    assert not partial["ok"]
    assert "username and password" in partial["error"].lower()
