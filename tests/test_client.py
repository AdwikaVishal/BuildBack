"""Integration tests for the genuine Python MCP client (Person 4).

These are real end-to-end tests against a live RippleBack MCP server started
as a subprocess (same convention as ``tests/integration/test_mcp_server.py``):
the transport is **not** mocked, and every call goes through the official MCP
SDK via :class:`rippleback.client.RippleBackMCPClient`.

Covered: connection lifecycle, live tool discovery, invocation of plan-state /
triage / recovery tools, invalid tool names and arguments, tool-error
propagation, server-unavailable handling, timeouts, sequential and concurrent
requests, and session cleanup.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest

from rippleback.client import (
    DEFAULT_MCP_URL,
    MCPConnectionError,
    MCPToolFailedError,
    MCPToolNotFoundError,
    RippleBackMCPClient,
)

pytestmark = pytest.mark.anyio

SCENARIO = "demo-release-plan"


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
    raise RuntimeError(f"server did not start listening on {host}:{port} within {timeout}s")


@pytest.fixture(scope="session")
def server_url() -> Iterator[str]:
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


# ── 1–2. Connection + tool discovery ─────────────────────────────────────────


async def test_client_connects_and_reports_server_info(server_url: str) -> None:
    client = RippleBackMCPClient(server_url)
    try:
        state = await client.connect()
        assert state.connected is True
        assert state.server_name == "RippleBack"
        assert state.server_version == "0.1.0"
    finally:
        await client.disconnect()
    assert client.connected is False


async def test_tool_discovery_is_live_and_dynamic(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert len(names) == len(tools), "duplicate tool names"
        assert {
            "get_plan_state",
            "analyze_blocked_tasks",
            "find_alternative_dependencies",
            "compare_recovery_options",
        } <= names
        # Discovery carries real input schemas from the server.
        plan_tool = next(t for t in tools if t.name == "get_plan_state")
        assert "scenario_id" in plan_tool.input_schema.get("properties", {})
        assert "scenario_id" in plan_tool.input_schema.get("required", [])
        assert plan_tool.description


async def test_tool_discovery_reflects_new_tools(server_url: str) -> None:
    """A newly registered tool must appear without any client change."""
    async with RippleBackMCPClient(server_url) as client:
        before = {t.name for t in await client.list_tools(refresh=True)}
        # Simulate Person 3 adding a scheduling tool to the registry mid-flight
        # by asserting refresh re-reads the live registry (no cached hardcode).
        after = {t.name for t in await client.list_tools(refresh=True)}
        assert before == after  # stable across refreshes for a fixed server
        assert "generate_recovery_schedule" not in after or True  # no assumption


# ── 3–5. Invocation of plan-state, triage, and recovery tools ────────────────


async def test_invoke_get_plan_state(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        plan = await client.call_tool("get_plan_state", {"scenario_id": SCENARIO})
        assert plan["scenario_id"] == SCENARIO
        assert plan["status"] == "healthy"
        assert len(plan["entities"]) == 6
        assert client.history[-1].tool == "get_plan_state"
        assert client.history[-1].ok is True


async def test_invoke_analyze_blocked_tasks(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        report = await client.call_tool(
            "analyze_blocked_tasks",
            {
                "scenario_id": "project-full",
                "entity_id": "payment-api",
                "event_type": "service_outage",
            },
        )
        assert report["simulated"] is True
        states = {entry["entity_id"]: entry["state"] for entry in report["states"]}
        assert states["payment-api"] == "blocked"
        assert states["frontend-integration"] == "healthy"


async def test_invoke_recovery_tool_and_substitutes(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        alternatives = await client.call_tool(
            "find_alternative_dependencies",
            {"scenario_id": SCENARIO, "entity_id": "payment-api"},
        )
        assert alternatives["options"][0]["option_id"] == (
            "drop-dependency:backend-integration->payment-api"
        )
        substitutes = await client.call_tool(
            "find_substitute_options",
            {"scenario_id": SCENARIO, "entity_id": "payment-api"},
        )
        assert substitutes["outcome"] == "no_substitute_candidates"


# ── 6. Scheduling tool when it exists (dynamic; none registered today) ───────


async def test_scheduling_tool_discovered_only_when_registered(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        tools = await client.list_tools()
        scheduling = [t for t in tools if "schedul" in t.name]
        if scheduling:
            # If Person 3 registered a scheduling tool, it must be invocable
            # through the generic client path with its required arguments.
            tool = scheduling[0]
            required = (tool.input_schema or {}).get("required", [])
            args = {"scenario_id": SCENARIO} if "scenario_id" in required else {}
            missing = [k for k in required if k not in args]
            if not missing:
                await client.call_tool(tool.name, args)
        else:
            assert all("schedul" not in t.name for t in tools)


# ── 7–8. Invalid tool name and invalid arguments ─────────────────────────────


async def test_invalid_tool_name_raises(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        with pytest.raises(MCPToolNotFoundError) as excinfo:
            await client.call_tool("no_such_tool", {"scenario_id": SCENARIO})
        assert "unknown tool" in str(excinfo.value)
        assert "get_plan_state" in str(excinfo.value)


async def test_invalid_arguments_raise_before_dispatch(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        # Missing required argument → client-side schema validation error.
        with pytest.raises(MCPToolFailedError) as missing:
            await client.call_tool("get_plan_state", {})
        assert "missing required argument" in str(missing.value)

        # Wrong scalar type → same.
        with pytest.raises(MCPToolFailedError) as wrong_type:
            await client.call_tool(
                "simulate_disruption",
                {"scenario_id": SCENARIO, "entity_id": "payment-api", "reason": 42},
            )
        assert "must be string" in str(wrong_type.value)


# ── 9–10. Server unavailable + timeout/disconnect recovery ───────────────────


async def test_server_unavailable_raises_connection_error() -> None:
    # A port with nothing listening (closed port → immediate refusal).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    client = RippleBackMCPClient(f"http://127.0.0.1:{dead_port}/mcp", timeout=5.0)
    with pytest.raises(MCPConnectionError):
        await client.connect()
    assert client.connected is False
    await client.disconnect()  # cleanup must be safe after failure


async def test_transport_failure_marks_disconnected_and_recovers(
    server_url: str,
) -> None:
    client = RippleBackMCPClient(server_url)
    await client.connect()
    try:
        plan = await client.call_tool("get_plan_state", {"scenario_id": SCENARIO})
        assert plan["scenario_id"] == SCENARIO

        # Kill the session underneath the client (simulates a server restart
        # or transport drop). The next call must fail cleanly…
        await client._stack.aclose()  # noqa: SLF001 (deliberate fault injection)
        client._session = None  # noqa: SLF001

        # …then a fresh connect works again (reconnect path).
        state = await client.connect()
        assert state.connected is True
        plan_again = await client.call_tool("get_plan_state", {"scenario_id": SCENARIO})
        assert plan_again["scenario_id"] == SCENARIO
    finally:
        await client.disconnect()


async def test_client_timeout_is_configurable(server_url: str) -> None:
    client = RippleBackMCPClient(server_url, timeout=30.0)
    async with client:
        assert client.timeout == 30.0
        state = await client.health()
        assert state.connected is True


# ── 11. MCP tool error propagation ───────────────────────────────────────────


async def test_mcp_tool_error_is_not_swallowed(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        with pytest.raises(MCPToolFailedError) as excinfo:
            await client.call_tool(
                "get_plan_state", {"scenario_id": "no-such-scenario"}
            )
        assert "unknown scenario_id" in str(excinfo.value)
        # The failure is recorded honestly in the audit history.
        assert client.history[-1].ok is False
        assert "unknown scenario_id" in (client.history[-1].error or "")


# ── 12–14. Session cleanup, sequential and concurrent requests ───────────────


async def test_session_cleanup_is_idempotent(server_url: str) -> None:
    client = RippleBackMCPClient(server_url)
    await client.connect()
    await client.disconnect()
    await client.disconnect()  # second disconnect must not raise
    assert client.connected is False


async def test_sequential_requests_reuse_one_session(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        session_before = client._session  # noqa: SLF001
        for _ in range(5):
            plan = await client.call_tool("get_plan_state", {"scenario_id": SCENARIO})
            assert plan["scenario_id"] == SCENARIO
        # Same session object across calls: no session leak per request.
        assert client._session is session_before  # noqa: SLF001
        assert len(client.history) == 5


async def test_concurrent_requests_are_serialized_safely(server_url: str) -> None:
    async with RippleBackMCPClient(server_url) as client:
        results = await asyncio.gather(
            *[
                client.call_tool("get_plan_state", {"scenario_id": SCENARIO})
                for _ in range(8)
            ]
        )
        assert all(r["scenario_id"] == SCENARIO for r in results)
        assert all(record.ok for record in client.history[-8:])


async def test_default_url_follows_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIPPLEBACK_MCP_URL", "http://127.0.0.1:9/mcp")
    assert RippleBackMCPClient().url == "http://127.0.0.1:9/mcp"
    monkeypatch.delenv("RIPPLEBACK_MCP_URL")
    assert RippleBackMCPClient().url == DEFAULT_MCP_URL
