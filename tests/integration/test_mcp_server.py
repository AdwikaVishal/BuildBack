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


MILESTONE6_TOOLS = {
    "recalculate_delivery_forecast",
    "validate_hard_constraints",
    "compare_recovery_options",
    "find_substitute_options",
    "generate_recovery_schedule",
}
DROP_OPTION_ID = "drop-dependency:backend-integration->payment-api"
DROP_CHANGE = {
    "action": "remove_dependency",
    "dependent": "backend-integration",
    "prerequisite": "payment-api",
}
CYCLE_CHANGE = {
    "action": "add_dependency",
    "dependent": "payment-api",
    "prerequisite": "production-release",
}


async def test_all_ten_tools_are_discoverable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_names = [tool.name for tool in result.tools]
            assert len(tool_names) == len(set(tool_names)), tool_names
            assert {
                "get_plan_state",
                "simulate_disruption",
                "find_alternative_dependencies",
                "evaluate_recovery_plan",
                "rebalance_workload",
                "analyze_blocked_tasks",
            } <= set(tool_names)
            assert MILESTONE6_TOOLS <= set(tool_names)


async def test_recalculate_delivery_forecast_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "recalculate_delivery_forecast",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": [DROP_CHANGE]},
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["simulated"] is True
            assert report["plan_version"] == 1
            assert report["feasible"] is True
            assert report["unit"] == "steps"
            assert report["baseline_length"] == 5.0
            assert report["forecast_length"] == 4.0
            assert report["delay_delta"] == -1.0
            assert report["assumptions"] and report["limitations"]

            via_id = await session.call_tool(
                "recalculate_delivery_forecast",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": DROP_OPTION_ID,
                    "disrupted_entity_id": "payment-api",
                },
            )
            assert via_id.is_error is False
            via_id_report = json.loads(via_id.content[0].text)
            assert via_id_report["feasible"] is True
            assert via_id_report["delay_delta"] == -1.0


async def test_recalculate_delivery_forecast_invalid_graph_stays_in_band(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "recalculate_delivery_forecast",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": [CYCLE_CHANGE]},
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["feasible"] is False
            assert report["error"]["code"] == "CYCLIC_DEPENDENCY"
            assert report["forecast_length"] is None
            assert report["delay_delta"] is None


async def test_recalculate_delivery_forecast_rejects_substitute_ids(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "recalculate_delivery_forecast",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "option_id": "substitute:payment-api->backup-api",
                },
            )
            assert result.is_error is True
            text = result.content[0].text if result.content else ""
            assert "explicit 'changes'" in text


async def test_recalculate_delivery_forecast_invalid_inputs_return_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            unknown = await session.call_tool(
                "recalculate_delivery_forecast",
                {"scenario_id": "no-such-scenario", "changes": []},
            )
            assert unknown.is_error is True
            assert "unknown scenario_id" in unknown.content[0].text

            malformed = await session.call_tool(
                "recalculate_delivery_forecast",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": "remove-me"},
            )
            assert malformed.is_error is True
            malformed_text = malformed.content[0].text
            assert "validation error" in malformed_text
            assert "changes" in malformed_text


async def test_validate_hard_constraints_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            baseline = await session.call_tool(
                "validate_hard_constraints", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            assert baseline.is_error is False
            report = json.loads(baseline.content[0].text)
            assert report["simulated"] is True
            assert report["feasible"] is True
            assert report["violations"] == []
            assert report["graph_errors"] == []

            infeasible = await session.call_tool(
                "validate_hard_constraints",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": [CYCLE_CHANGE]},
            )
            assert infeasible.is_error is False
            infeasible_report = json.loads(infeasible.content[0].text)
            assert infeasible_report["feasible"] is False
            codes = [v["code"] for v in infeasible_report["violations"]]
            assert codes == ["CYCLIC_DEPENDENCY"]


async def test_validate_hard_constraints_invalid_inputs_return_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            unknown = await session.call_tool(
                "validate_hard_constraints", {"scenario_id": "no-such-scenario"}
            )
            assert unknown.is_error is True
            assert "unknown scenario_id" in unknown.content[0].text

            malformed = await session.call_tool(
                "validate_hard_constraints",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": 42},
            )
            assert malformed.is_error is True
            malformed_text = malformed.content[0].text
            assert "validation error" in malformed_text
            assert "changes" in malformed_text


async def test_compare_recovery_options_callable(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            alternatives = await session.call_tool(
                "find_alternative_dependencies",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "payment-api"},
            )
            assert alternatives.is_error is False
            options = json.loads(alternatives.content[0].text)["options"]
            assert options

            result = await session.call_tool(
                "compare_recovery_options",
                {"scenario_id": SEEDED_SCENARIO_ID, "options": options},
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["simulated"] is True
            assert report["outcome"] == "ranked"
            assert report["recommendation"] == DROP_OPTION_ID
            top = report["ranked"][0]
            assert top["feasible"] is True
            assert top["forecast"]["delay_delta"] == -1.0
            assert report["baseline"]["length"] == 5.0
            assert report["weights"]["delay"] == 1.0


async def test_compare_recovery_options_never_recommends_infeasible(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            alternatives = await session.call_tool(
                "find_alternative_dependencies",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "payment-api"},
            )
            options = json.loads(alternatives.content[0].text)["options"]
            options.insert(
                0,
                {
                    "option_id": "invalid-cycle-option",
                    "changes": [CYCLE_CHANGE],
                },
            )

            result = await session.call_tool(
                "compare_recovery_options",
                {"scenario_id": SEEDED_SCENARIO_ID, "options": options},
            )
            assert result.is_error is False
            report = json.loads(result.content[0].text)
            assert report["outcome"] == "ranked"
            assert report["recommendation"] == DROP_OPTION_ID
            excluded = report["excluded"][0]
            assert excluded["option_id"] == "invalid-cycle-option"
            assert excluded["feasible"] is False
            assert [v["code"] for v in excluded["violations"]] == [
                "CYCLIC_DEPENDENCY"
            ]

            only_bad = await session.call_tool(
                "compare_recovery_options",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "options": [
                        {"option_id": "invalid-cycle-option", "changes": [CYCLE_CHANGE]}
                    ],
                },
            )
            assert only_bad.is_error is False
            only_bad_report = json.loads(only_bad.content[0].text)
            assert only_bad_report["outcome"] == "no_feasible_recovery"
            assert only_bad_report["recommendation"] is None


async def test_compare_recovery_options_invalid_inputs_return_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            unknown = await session.call_tool(
                "compare_recovery_options",
                {"scenario_id": "no-such-scenario", "options": []},
            )
            assert unknown.is_error is True
            assert "unknown scenario_id" in unknown.content[0].text

            not_a_list = await session.call_tool(
                "compare_recovery_options",
                {"scenario_id": SEEDED_SCENARIO_ID, "options": "not-a-list"},
            )
            assert not_a_list.is_error is True
            not_a_list_text = not_a_list.content[0].text
            assert "validation error" in not_a_list_text
            assert "options" in not_a_list_text

            bad_weight = await session.call_tool(
                "compare_recovery_options",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "options": [{"option_id": DROP_OPTION_ID}],
                    "weights": {"delay": -1.0},
                },
            )
            assert bad_weight.is_error is True
            assert "weight" in bad_weight.content[0].text


async def test_find_substitute_options_no_candidates_on_fixtures(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for scenario_id in (
                SEEDED_SCENARIO_ID,
                STAFFED_SCENARIO_ID,
                PROJECT_FULL_SCENARIO_ID,
                TRAVEL_SCENARIO_ID,
            ):
                entity_id = (
                    "leg-1" if scenario_id == TRAVEL_SCENARIO_ID else "payment-api"
                )
                result = await session.call_tool(
                    "find_substitute_options",
                    {"scenario_id": scenario_id, "entity_id": entity_id},
                )
                assert result.is_error is False, scenario_id
                report = json.loads(result.content[0].text)
                assert report["simulated"] is True
                assert report["outcome"] == "no_substitute_candidates"
                assert report["options"] == []
                assert report["missing_data"]


async def test_find_substitute_options_unknown_inputs_return_error(
    server_url: str,
) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            unknown_scenario = await session.call_tool(
                "find_substitute_options",
                {"scenario_id": "no-such-scenario", "entity_id": "payment-api"},
            )
            assert unknown_scenario.is_error is True
            assert "unknown scenario_id" in unknown_scenario.content[0].text

            unknown_entity = await session.call_tool(
                "find_substitute_options",
                {
                    "scenario_id": SEEDED_SCENARIO_ID,
                    "entity_id": "no-such-entity",
                },
            )
            assert unknown_entity.is_error is True
            assert "unknown entity_id" in unknown_entity.content[0].text


async def test_milestone6_tools_do_not_mutate_plan(server_url: str) -> None:
    async with streamable_http_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            before = await session.call_tool(
                "get_plan_state", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            baseline = json.loads(before.content[0].text)

            await session.call_tool(
                "recalculate_delivery_forecast",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": [DROP_CHANGE]},
            )
            await session.call_tool(
                "validate_hard_constraints",
                {"scenario_id": SEEDED_SCENARIO_ID, "changes": [CYCLE_CHANGE]},
            )
            alternatives = await session.call_tool(
                "find_alternative_dependencies",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "payment-api"},
            )
            options = json.loads(alternatives.content[0].text)["options"]
            await session.call_tool(
                "compare_recovery_options",
                {"scenario_id": SEEDED_SCENARIO_ID, "options": options},
            )
            await session.call_tool(
                "find_substitute_options",
                {"scenario_id": SEEDED_SCENARIO_ID, "entity_id": "payment-api"},
            )
            await session.call_tool(
                "generate_recovery_schedule",
                {"scenario_id": SEEDED_SCENARIO_ID},
            )

            after = await session.call_tool(
                "get_plan_state", {"scenario_id": SEEDED_SCENARIO_ID}
            )
            committed = json.loads(after.content[0].text)
            assert committed == baseline
            assert committed["status"] == "healthy"
            assert committed["version"] == 1
            assert committed["disruptions"] == []
            assert len(committed["dependencies"]) == 4
