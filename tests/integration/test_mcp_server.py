"""Integration tests for the RippleBack MCP server.

These are real, end-to-end tests: they use the official Python MCP client SDK
against a live Streamable HTTP endpoint. The transport is **not** mocked.

Two modes:

* If ``RIPPLEBACK_TEST_URL`` is set, the tests connect to that already-running
  server (the "two terminals" workflow).
* Otherwise a fresh server subprocess is started on a free localhost port for
  the duration of the session.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.anyio

SEEDED_SCENARIO_ID = "demo-release-plan"
EXPECTED_ENTITIES = {
    "payment-api",
    "backend-integration",
    "integration-testing",
    "frontend-integration",
    "release-validation",
    "production-release",
}
EXPECTED_DEPENDENCIES = {
    ("backend-integration", "payment-api"),
    ("integration-testing", "backend-integration"),
    ("release-validation", "integration-testing"),
    ("production-release", "release-validation"),
}


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


async def test_get_plan_state_is_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = [tool.name for tool in result.tools]
            assert "get_plan_state" in tool_names, f"tools were {tool_names}"


async def test_get_plan_state_seeded_scenario(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_plan_state", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            assert result.is_error is False
            assert result.content, "expected tool content"
            plan = json.loads(result.content[0].text)

            assert plan["scenario_id"] == SEEDED_SCENARIO_ID
            assert plan["status"] == "healthy"
            assert {entity["id"] for entity in plan["entities"]} == EXPECTED_ENTITIES
            actual_deps = {
                (dep["dependent"], dep["prerequisite"]) for dep in plan["dependencies"]
            }
            assert actual_deps == EXPECTED_DEPENDENCIES


async def test_get_plan_state_unknown_scenario_returns_error(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_plan_state", {"scenario_id": "no-such-scenario"}
            )
            assert result.is_error is True
            text = result.content[0].text if result.content else ""
            assert "unknown scenario_id" in text


async def test_simulate_disruption_is_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = {tool.name for tool in result.tools}
            assert {"get_plan_state", "simulate_disruption"} <= tool_names


async def test_simulate_disruption_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "simulate_disruption",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "entity_id": "payment-api",
                    "reason": "service outage",
                    "severity": "high",
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)

            assert report["simulated"] is True
            assert report["plan_version"] == 1
            direct = {e["entity_id"] for e in report["directly_affected"]}
            transitive = {e["entity_id"] for e in report["transitively_affected"]}
            unaffected = {e["entity_id"]: e["state"] for e in report["unaffected"]}
            assert direct == {"backend-integration"}
            assert transitive == {
                "integration-testing",
                "release-validation",
                "production-release",
            }
            assert unaffected.get("frontend-integration") == "in_progress"


async def test_simulate_disruption_does_not_mutate_plan(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool(
                "simulate_disruption",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "entity_id": "payment-api",
                    "reason": "service outage",
                },
            )
            plan = await session.call_tool(
                "get_plan_state", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            committed = json.loads(plan.content[0].text)
            assert committed["status"] == "healthy"
            assert committed["version"] == 1
            assert committed["disruptions"] == []


async def test_simulate_disruption_unknown_entity_returns_error(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "simulate_disruption",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "entity_id": "no-such-entity",
                    "reason": "service outage",
                },
            )
            assert result.is_error is True
            text = result.content[0].text if result.content else ""
            assert "unknown entity_id" in text


async def test_find_alternative_dependencies_is_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = {tool.name for tool in result.tools}
            assert {
                "get_plan_state",
                "simulate_disruption",
                "find_alternative_dependencies",
            } <= tool_names


async def test_find_alternative_dependencies_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "find_alternative_dependencies",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "payment-api"},
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)

            assert report["simulated"] is True
            assert report["plan_version"] == 1
            options = report["options"]
            assert len(options) == 1
            option = options[0]
            assert option["option_id"] == "drop-dependency:backend-integration->payment-api"
            assert option["affected_dependency"] == {
                "dependent": "backend-integration",
                "prerequisite": "payment-api",
            }
            assert option["alternative"]["action"] == "remove_dependency"
            assert option["assumptions"] and option["limitations"]
            assert "frontend-integration" not in option["unblocks"]


async def test_find_alternative_dependencies_unknown_entity_returns_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "find_alternative_dependencies",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "no-such-entity"},
            )
            assert result.is_error is True
            text = result.content[0].text if result.content else ""
            assert "unknown entity_id" in text


async def test_find_alternative_dependencies_does_not_mutate_plan(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool(
                "find_alternative_dependencies",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "payment-api"},
            )
            plan = await session.call_tool(
                "get_plan_state", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            committed = json.loads(plan.content[0].text)
            assert committed["status"] == "healthy"
            assert committed["version"] == 1
            assert committed["disruptions"] == []
            assert len(committed["dependencies"]) == 4


async def test_evaluate_recovery_plan_is_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = {tool.name for tool in result.tools}
            assert {
                "get_plan_state",
                "simulate_disruption",
                "find_alternative_dependencies",
                "evaluate_recovery_plan",
            } <= tool_names


async def test_evaluate_recovery_plan_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "evaluate_recovery_plan",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": "drop-dependency:backend-integration->payment-api",
                    "expected_plan_version": 1,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)

            assert report["verdict"] == "structurally_valid"
            assert report["plan_version"] == 1
            assert report["dependency"] == {
                "dependent": "backend-integration",
                "prerequisite": "payment-api",
            }
            assert report["impact"]["unblocks"] == [
                "backend-integration",
                "integration-testing",
                "release-validation",
                "production-release",
            ]
            assert report["assumptions"] and report["limitations"]


async def test_evaluate_recovery_plan_missing_edge_is_rejected(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "evaluate_recovery_plan",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": "drop-dependency:payment-api->backend-integration",
                    "expected_plan_version": 1,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["verdict"] == "rejected"
            assert report["impact"] is None
            assert any(
                finding["code"] == "missing_dependency"
                for finding in report["findings"]
            )


async def test_evaluate_recovery_plan_stale_version_is_rejected(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "evaluate_recovery_plan",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": "drop-dependency:backend-integration->payment-api",
                    "expected_plan_version": 99,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["verdict"] == "rejected"
            assert any(
                finding["code"] == "stale_plan_version"
                for finding in report["findings"]
            )


async def test_evaluate_recovery_plan_malformed_option_returns_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "evaluate_recovery_plan",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": "not-an-option",
                    "expected_plan_version": 1,
                },
            )
            assert result.is_error is True
            text = result.content[0].text if result.content else ""
            assert "malformed option_id" in text


async def test_evaluate_recovery_plan_does_not_mutate_plan(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool(
                "evaluate_recovery_plan",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": "drop-dependency:backend-integration->payment-api",
                    "expected_plan_version": 1,
                },
            )
            plan = await session.call_tool(
                "get_plan_state", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            committed = json.loads(plan.content[0].text)
            assert committed["status"] == "healthy"
            assert committed["version"] == 1
            assert committed["disruptions"] == []
            assert len(committed["dependencies"]) == 4


STAFFED_SCENARIO_ID = "demo-release-plan-staffed"


async def test_rebalance_workload_is_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = {tool.name for tool in result.tools}
            assert {
                "get_plan_state",
                "simulate_disruption",
                "find_alternative_dependencies",
                "evaluate_recovery_plan",
                "rebalance_workload",
            } <= tool_names


async def test_rebalance_workload_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "rebalance_workload",
                {
                    "scenario_id": STAFFED_SCENARIO_ID,
                    "expected_plan_version": 1,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)

            assert report["outcome"] == "ok"
            assert report["simulated"] is True
            assert report["unit"] == "work_units"
            assert report["plan_version"] == 1
            suggestions = report["suggestions"]
            assert len(suggestions) == 1
            assert suggestions[0]["suggestion_id"] == "rebalance:production-release->bob"
            assert suggestions[0]["from_resource_id"] == "alice"
            assert suggestions[0]["to_resource_id"] == "bob"
            by_id = {r["resource_id"]: r for r in report["resources"]}
            assert by_id["alice"]["overloaded"] is True
            assert by_id["bob"]["overloaded"] is False


async def test_rebalance_workload_insufficient_data(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "rebalance_workload",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "expected_plan_version": 1,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["outcome"] == "insufficient_data"
            assert report["suggestions"] == []


async def test_rebalance_workload_invalid_inputs_return_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            unknown = await session.call_tool(
                "rebalance_workload",
                {
                    "scenario_id": STAFFED_SCENARIO_ID,
                    "expected_plan_version": 1,
                    "resource_id": "no-such-resource",
                },
            )
            assert unknown.is_error is True
            assert "unknown resource_id" in unknown.content[0].text

            stale = await session.call_tool(
                "rebalance_workload",
                {
                    "scenario_id": STAFFED_SCENARIO_ID,
                    "expected_plan_version": 99,
                },
            )
            assert stale.is_error is True
            assert "stale expected_plan_version" in stale.content[0].text


async def test_rebalance_workload_does_not_mutate_plan(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            before = await session.call_tool(
                "get_plan_state", {"scenario_id": STAFFED_SCENARIO_ID}
            )
            baseline = json.loads(before.content[0].text)
            await session.call_tool(
                "rebalance_workload",
                {
                    "scenario_id": STAFFED_SCENARIO_ID,
                    "expected_plan_version": 1,
                },
            )
            after = await session.call_tool(
                "get_plan_state", {"scenario_id": STAFFED_SCENARIO_ID}
            )
            committed = json.loads(after.content[0].text)
            assert committed == baseline
            assert committed["version"] == 1


PROJECT_FULL_SCENARIO_ID = "project-full"
TRAVEL_SCENARIO_ID = "travel"


async def test_analyze_blocked_tasks_is_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = {tool.name for tool in result.tools}
            assert {
                "get_plan_state",
                "simulate_disruption",
                "find_alternative_dependencies",
                "evaluate_recovery_plan",
                "rebalance_workload",
                "analyze_blocked_tasks",
            } <= tool_names


async def test_analyze_blocked_tasks_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "analyze_blocked_tasks",
                {
                    "scenario_id": PROJECT_FULL_SCENARIO_ID,
                    "entity_id": "payment-api",
                    "event_type": "service_outage",
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)

            assert report["simulated"] is True
            assert report["plan_version"] == 1
            states = {entry["entity_id"]: entry["state"] for entry in report["states"]}
            assert len(report["states"]) == 10
            assert states["payment-api"] == "blocked"
            assert states["auth-service"] == "done"
            assert states["frontend-integration"] == "healthy"
            assert states["unit-testing"] == "at_risk"
            parallel = {entry["entity_id"] for entry in report["parallel_work"]}
            assert "frontend-integration" in parallel
            assert "production-release" not in parallel


async def test_analyze_blocked_tasks_external_delay(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "analyze_blocked_tasks",
                {
                    "scenario_id": PROJECT_FULL_SCENARIO_ID,
                    "entity_id": "vendor-delivery",
                    "event_type": "delivery_delay",
                    "delay_minutes": 480,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            states = {entry["entity_id"]: entry["state"] for entry in report["states"]}
            assert states["vendor-delivery"] == "external"
            assert states["backend-integration"] == "external"


async def test_analyze_blocked_tasks_travel_delay_absorbs(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "analyze_blocked_tasks",
                {
                    "scenario_id": TRAVEL_SCENARIO_ID,
                    "entity_id": "leg-1",
                    "event_type": "transport_delay",
                    "delay_minutes": 120,
                },
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            states = {entry["entity_id"]: entry["state"] for entry in report["states"]}
            assert states["connecting-leg"] == "blocked"
            assert states["hotel-check-in"] == "at_risk"
            assert states["meeting"] == "healthy"


async def test_analyze_blocked_tasks_unknown_entity_returns_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "analyze_blocked_tasks",
                {"scenario_id": PROJECT_FULL_SCENARIO_ID, "entity_id": "no-such-entity"},
            )
            assert result.is_error is True
            text = result.content[0].text if result.content else ""
            assert "unknown entity_id" in text


async def test_analyze_blocked_tasks_does_not_mutate_plan(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            before = await session.call_tool(
                "get_plan_state", {"scenario_id": PROJECT_FULL_SCENARIO_ID}
            )
            baseline = json.loads(before.content[0].text)
            await session.call_tool(
                "analyze_blocked_tasks",
                {
                    "scenario_id": PROJECT_FULL_SCENARIO_ID,
                    "entity_id": "payment-api",
                },
            )
            after = await session.call_tool(
                "get_plan_state", {"scenario_id": PROJECT_FULL_SCENARIO_ID}
            )
            committed = json.loads(after.content[0].text)
            assert committed == baseline
            assert committed["version"] == 1
