"""Integration tests for the local API adapter (Person 4).

These tests start the **real** adapter (Starlette app bound to localhost) in a
thread against a live MCP server subprocess, then exercise its REST surface
over actual HTTP — health, live tool discovery, tool invocation, assistant
chat, malformed input handling, and MCP-offline behaviour.

The MCP transport is not mocked: the adapter talks to the server through the
official Python MCP SDK, exactly as in production.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn

from rippleback import adapter
from rippleback.adapter import app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_listening(host: str, port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"nothing listening on {host}:{port} within {timeout}s")


def _request(
    base: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


@pytest.fixture(scope="session")
def live_server_url() -> Iterator[str]:
    existing = os.environ.get("RIPPLEBACK_TEST_URL")
    if existing:
        yield existing
        return
    host, port = "127.0.0.1", _free_port()
    env = {**os.environ, "RIPPLEBACK_HOST": host, "RIPPLEBACK_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "rippleback.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until_listening(host, port)
        yield f"http://{host}:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def adapter_base_url(live_server_url: str) -> Iterator[str]:
    """Run the real adapter on localhost against the live MCP server."""
    # Reset the process-wide client so it binds to the test server URL.
    adapter._client = None  # noqa: SLF001
    adapter._assistants.clear()  # noqa: SLF001
    os.environ["RIPPLEBACK_MCP_URL"] = live_server_url

    host, port = "127.0.0.1", _free_port()
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_until_listening(host, port)
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        adapter._client = None  # noqa: SLF001
        adapter._assistants.clear()  # noqa: SLF001
        os.environ.pop("RIPPLEBACK_MCP_URL", None)


# ── 1. Health ─────────────────────────────────────────────────────────────────


def test_health_reports_mcp_status_and_discovered_tools(adapter_base_url: str) -> None:
    status, body = _request(adapter_base_url, "/api/health")
    assert status == 200
    assert body["name"] == "RippleBack"
    assert body["adapter"]["name"] == "rippleback-adapter"
    assert body["mcp"]["connected"] is True
    assert "get_plan_state" in body["tools"]
    assert len(body["tools"]) >= 10
    assert body["assistant"]["provider"] in ("deterministic", "bedrock")


# ── 2. Live tool discovery with schemas ───────────────────────────────────────


def test_tools_endpoint_returns_schemas(adapter_base_url: str) -> None:
    status, body = _request(adapter_base_url, "/api/mcp/tools")
    assert status == 200
    assert body["connected"] is True
    names = [t["name"] for t in body["tools"]]
    assert len(names) == len(set(names)), "duplicate tools discovered"
    plan_tool = next(t for t in body["tools"] if t["name"] == "get_plan_state")
    assert "scenario_id" in plan_tool["input_schema"]["required"]
    assert plan_tool["description"]


def test_tools_refresh_parameter_forces_rediscovery(adapter_base_url: str) -> None:
    status, body = _request(adapter_base_url, "/api/mcp/tools?refresh=1")
    assert status == 200
    assert body["count"] >= 10


# ── 3–4. Tool invocation over HTTP ────────────────────────────────────────────


def test_tool_invocation_returns_structured_result(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "get_plan_state", "arguments": {"scenario_id": "demo-release-plan"}},
    )
    assert status == 200
    assert body["isError"] is False
    assert body["result"]["scenario_id"] == "demo-release-plan"
    assert len(body["result"]["entities"]) == 6


def test_assistant_chat_runs_real_mcp_tool_calls(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url,
        "/api/assistant/chat",
        method="POST",
        payload={"message": "Show me the current plan.", "scenario_id": "demo-release-plan"},
    )
    assert status == 200
    assert body["intent"] == "plan_state"
    assert body["needs_clarification"] is False
    assert [s["tool"] for s in body["steps"]] == ["get_plan_state"]
    assert body["steps"][0]["status"] == "ok"
    assert body["tool_calls_made"] == 1
    assert "plan-payment-api-release" in body["reply"]


def test_assistant_chat_multi_tool_workflow(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url,
        "/api/assistant/chat",
        method="POST",
        payload={
            "message": "The payment API is down. What is blocked, find recovery "
            "alternatives and compare the options.",
            "scenario_id": "demo-release-plan",
        },
    )
    assert status == 200
    executed = [s["tool"] for s in body["steps"] if s["status"] == "ok"]
    assert executed == [
        "get_plan_state",
        "analyze_blocked_tasks",
        "find_alternative_dependencies",
        "compare_recovery_options",
        "generate_recovery_schedule",
    ]
    assert body["provider"] == "deterministic"
    assert "read-only" in body["reply"].lower()


# ── 5–6. Error propagation (not transformed into success) ─────────────────────


def test_unknown_tool_returns_404_with_clear_error(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "no_such_tool", "arguments": {}},
    )
    assert status == 404
    assert body["isError"] is True
    assert "unknown tool" in body["error"]


def test_invalid_arguments_return_clear_error(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "get_plan_state", "arguments": {}},
    )
    assert body["isError"] is True
    assert "missing required argument" in body["error"]


def test_tool_error_from_mcp_server_is_reported_as_error(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "get_plan_state", "arguments": {"scenario_id": "nope"}},
    )
    assert status == 200
    assert body["isError"] is True
    assert "unknown scenario_id" in body["error"]


def test_malformed_requests_are_rejected(adapter_base_url: str) -> None:
    status, body = _request(
        adapter_base_url, "/api/mcp/tool", method="POST", payload={"arguments": {}}
    )
    assert status == 400
    assert body["isError"] is True

    status, body = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "get_plan_state", "arguments": "not-an-object"},
    )
    assert status == 400

    status, body = _request(
        adapter_base_url, "/api/assistant/chat", method="POST", payload={"message": "   "}
    )
    assert status == 400
    assert "message" in body["error"]


# ── 7. Read-only guarantee across assistant turns ─────────────────────────────


def test_assistant_never_mutates_committed_plan(adapter_base_url: str) -> None:
    _, before = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "get_plan_state", "arguments": {"scenario_id": "project-full"}},
    )
    status, chat = _request(
        adapter_base_url,
        "/api/assistant/chat",
        method="POST",
        payload={"message": "What is blocked if the payment API goes down? Then "
                 "find recovery alternatives and compare them.",
                 "scenario_id": "project-full"},
    )
    assert status == 200
    _, after = _request(
        adapter_base_url,
        "/api/mcp/tool",
        method="POST",
        payload={"tool": "get_plan_state", "arguments": {"scenario_id": "project-full"}},
    )
    assert before["result"]["version"] == after["result"]["version"]
    assert before["result"]["status"] == after["result"]["status"]
    assert after["result"]["disruptions"] == []


# ── 8. MCP-offline behaviour (adapter up, server down) ────────────────────────


def test_health_reports_503_when_mcp_unreachable() -> None:
    adapter._client = None  # noqa: SLF001
    adapter._assistants.clear()  # noqa: SLF001
    dead_port = _free_port()
    os.environ["RIPPLEBACK_MCP_URL"] = f"http://127.0.0.1:{dead_port}/mcp"

    host, port = "127.0.0.1", _free_port()
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_until_listening(host, port)
        base = f"http://{host}:{port}"

        status, body = _request(base, "/api/health")
        assert status == 503
        assert body["mcp"]["connected"] is False

        status, body = _request(
            base, "/api/mcp/tool", method="POST",
            payload={"tool": "get_plan_state", "arguments": {"scenario_id": "travel"}},
        )
        assert status == 503
        assert body["isError"] is True

        # The assistant still answers — with an honest "unreachable" reply.
        status, chat = _request(
            base, "/api/assistant/chat", method="POST",
            payload={"message": "Show me the current plan.", "scenario_id": "travel"},
        )
        assert status == 200
        assert "unreachable" in chat["reply"].lower()
        assert chat["steps"] == []
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        adapter._client = None  # noqa: SLF001
        adapter._assistants.clear()  # noqa: SLF001
        os.environ.pop("RIPPLEBACK_MCP_URL", None)
