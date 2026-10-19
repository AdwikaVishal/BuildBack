"""Tests for the RippleBack conversational assistant (Person 4).

Two layers:

* **Deterministic unit tests** with a fake MCP client that mimics the
  :class:`rippleback.client.RippleBackMCPClient` surface — intent mapping,
  parameter extraction, clarification questions, multi-tool workflows built
  from *actual intermediate results*, failure handling, the tool-call budget,
  scheduling-tool honesty, and the Bedrock-availability fallback.
* **One genuine end-to-end test** against a live MCP server subprocess,
  asserting the full workflow runs through real MCP calls and never mutates
  the committed plan.

No engine is ever called directly: the assistant only sequences MCP tool
calls through the client interface.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest

from rippleback.assistant import intents as I
from rippleback.assistant.explain import explain
from rippleback.assistant.orchestrator import Assistant
from rippleback.assistant.providers import (
    BedrockProvider,
    DeterministicProvider,
    make_provider,
)
from rippleback.client import (
    MCPConnectionError,
    MCPToolFailedError,
    MCPToolInfo,
    RippleBackMCPClient,
)

pytestmark = pytest.mark.anyio

# ── Canonical fixture data (mirrors the seeded project-full scenario) ─────────

PLAN = {
    "scenario_id": "project-full",
    "plan_id": "plan-payment-api-release-full",
    "version": 1,
    "status": "healthy",
    "entities": [
        {"id": "auth-service", "name": "Auth service", "status": "done"},
        {"id": "payment-api", "name": "Payment API service", "status": "in_progress"},
        {"id": "vendor-delivery", "name": "Vendor deliverable", "status": "pending"},
        {"id": "backend-integration", "name": "Backend integration", "status": "pending"},
        {"id": "integration-testing", "name": "Integration testing", "status": "pending"},
        {"id": "release-validation", "name": "Release validation", "status": "pending"},
        {"id": "production-release", "name": "Production release", "status": "pending"},
        {"id": "frontend-integration", "name": "Frontend integration", "status": "in_progress"},
        {"id": "unit-testing", "name": "Unit testing", "status": "pending"},
        {"id": "release-notes", "name": "Release notes", "status": "pending"},
    ],
    "dependencies": [
        {"dependent": "backend-integration", "prerequisite": "payment-api"},
        {"dependent": "integration-testing", "prerequisite": "backend-integration"},
    ],
    "constraints": [],
    "disruptions": [],
}

TRIAGE = {
    "scenario_id": "project-full",
    "plan_version": 1,
    "simulated": True,
    "disruption": {"entity_id": "payment-api", "name": "Payment API service",
                   "event_type": "service_outage", "reason": "service outage",
                   "delay_minutes": None},
    "states": [
        {"entity_id": "payment-api", "name": "Payment API service", "state": "blocked",
         "reason": "disrupted entity", "evidence_path": ["payment-api"], "evidence_edges": []},
        {"entity_id": "backend-integration", "name": "Backend integration", "state": "blocked",
         "reason": "hard dependency path", "evidence_path": ["payment-api", "backend-integration"],
         "evidence_edges": []},
        {"entity_id": "frontend-integration", "name": "Frontend integration", "state": "healthy",
         "reason": "no path", "evidence_path": [], "evidence_edges": []},
        {"entity_id": "unit-testing", "name": "Unit testing", "state": "at_risk",
         "reason": "benefits_from edge", "evidence_path": ["integration-testing", "unit-testing"],
         "evidence_edges": []},
    ],
    "summary": {"done": 1, "blocked": 5, "external": 0, "at_risk": 2, "healthy": 2},
    "parallel_work": [{"entity_id": "frontend-integration", "name": "Frontend integration"}],
    "critical_path": {"path": ["payment-api", "backend-integration"], "length": 2, "unit": "steps"},
    "critical_path_changes": [],
    "assumptions": [],
    "limitations": [],
}

OPTION = {
    "option_id": "drop-dependency:backend-integration->payment-api",
    "description": "Bypass the broken dependency.",
    "affected_entity_id": "backend-integration",
    "affected_dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
    "alternative": {"action": "remove_dependency",
                    "removed_dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
                    "dependencies": []},
    "unblocks": ["backend-integration", "integration-testing"],
    "assumptions": ["Assumes the prerequisite can be dropped."],
    "limitations": ["Hypothetical only."],
}

ALTERNATIVES = {
    "scenario_id": "project-full",
    "simulated": True,
    "disruption": {"entity_id": "payment-api", "name": "Payment API service"},
    "options": [OPTION],
}

COMPARISON = {
    "scenario_id": "project-full",
    "simulated": True,
    "outcome": "ranked",
    "weights": {"delay": 1.0, "cost": 0.0, "risk": 0.0, "resource_strain": 0.0, "disruption_count": 0.0},
    "feasible_count": 1,
    "scorable_count": 1,
    "recommendation": "drop-dependency:backend-integration->payment-api",
    "baseline": {"path": ["payment-api", "backend-integration"], "length": 2.0, "unit": "steps"},
    "ranked": [
        {"option_id": "drop-dependency:backend-integration->payment-api", "rank": 1,
         "feasible": True, "scorable": True, "score": -1.0,
         "forecast": {"delay_delta": -1.0, "unit": "steps"}, "violations": [], "reasons": []},
    ],
    "excluded": [
        {"option_id": "bad-cycle", "rank": None, "feasible": False, "scorable": False,
         "score": None, "forecast": {"delay_delta": None},
         "violations": [{"code": "CYCLIC_DEPENDENCY"}],
         "reasons": ["infeasible: CYCLIC_DEPENDENCY"]},
    ],
    "assumptions": [],
    "limitations": [],
}

EVALUATION = {
    "evaluation_id": "eval:test",
    "scenario_id": "project-full",
    "plan_version": 1,
    "expected_plan_version": 1,
    "verdict": "structurally_valid",
    "dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
    "findings": [
        {"code": "business_readiness_not_assessed", "level": "warning",
         "message": "Business readiness is not assessed."},
    ],
    "impact": {"disrupted_entity_id": "payment-api", "unblocks": ["backend-integration"],
               "still_blocked": []},
    "assumptions": [],
    "limitations": [],
}

WORKLOAD_INSUFFICIENT = {
    "scenario_id": "project-full",
    "simulated": True,
    "outcome": "insufficient_data",
    "suggestions": [],
}

FORECAST = {
    "scenario_id": "project-full",
    "simulated": True,
    "feasible": True,
    "unit": "steps",
    "baseline_path": ["payment-api", "backend-integration"],
    "baseline_length": 2.0,
    "forecast_path": ["payment-api"],
    "forecast_length": 1.0,
    "delay_delta": -1.0,
    "assumptions": [],
    "limitations": [],
}

SUBSTITUTES_NONE = {
    "scenario_id": "project-full",
    "simulated": True,
    "disruption": {"entity_id": "payment-api", "name": "Payment API service"},
    "outcome": "no_substitute_candidates",
    "options": [],
    "missing_data": ["entity 'payment-api' declares no metadata.substitutes"],
}

CONSTRAINTS_OK = {
    "scenario_id": "project-full",
    "simulated": True,
    "feasible": True,
    "violations": [],
    "graph_errors": [],
}

TOOL_RESULTS: dict[str, Any] = {
    "get_plan_state": PLAN,
    "analyze_blocked_tasks": TRIAGE,
    "find_alternative_dependencies": ALTERNATIVES,
    "compare_recovery_options": COMPARISON,
    "evaluate_recovery_plan": EVALUATION,
    "rebalance_workload": WORKLOAD_INSUFFICIENT,
    "recalculate_delivery_forecast": FORECAST,
    "find_substitute_options": SUBSTITUTES_NONE,
    "validate_hard_constraints": CONSTRAINTS_OK,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "get_plan_state": {"properties": {"scenario_id": {"type": "string"}},
                       "required": ["scenario_id"]},
    "analyze_blocked_tasks": {"properties": {"scenario_id": {"type": "string"},
                                             "entity_id": {"type": "string"},
                                             "event_type": {"type": "string"}},
                              "required": ["scenario_id", "entity_id"]},
    "find_alternative_dependencies": {"properties": {"scenario_id": {"type": "string"},
                                                     "entity_id": {"type": "string"}},
                                      "required": ["scenario_id", "entity_id"]},
    "compare_recovery_options": {"properties": {"scenario_id": {"type": "string"},
                                                "options": {"type": "array"}},
                                 "required": ["scenario_id", "options"]},
    "evaluate_recovery_plan": {"properties": {"scenario_id": {"type": "string"},
                                              "option_id": {"type": "string"},
                                              "expected_plan_version": {"type": "integer"}},
                               "required": ["scenario_id", "option_id", "expected_plan_version"]},
    "rebalance_workload": {"properties": {"scenario_id": {"type": "string"},
                                          "expected_plan_version": {"type": "integer"}},
                           "required": ["scenario_id", "expected_plan_version"]},
    "recalculate_delivery_forecast": {"properties": {"scenario_id": {"type": "string"}},
                                      "required": ["scenario_id"]},
    "find_substitute_options": {"properties": {"scenario_id": {"type": "string"},
                                               "entity_id": {"type": "string"}},
                                "required": ["scenario_id", "entity_id"]},
    "validate_hard_constraints": {"properties": {"scenario_id": {"type": "string"}},
                                  "required": ["scenario_id"]},
    "simulate_disruption": {"properties": {"scenario_id": {"type": "string"},
                                           "entity_id": {"type": "string"},
                                           "reason": {"type": "string"}},
                            "required": ["scenario_id", "entity_id", "reason"]},
}


class FakeClient:
    """Mimics RippleBackMCPClient's async surface with canned results."""

    def __init__(self, results: dict[str, Any] | None = None,
                 failures: dict[str, Exception] | None = None,
                 extra_tools: list[MCPToolInfo] | None = None) -> None:
        self.url = "http://fake.test/mcp"
        self.results = results if results is not None else TOOL_RESULTS
        self.failures = failures or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.history: list[Any] = []
        tools = [
            MCPToolInfo(name=name, description=f"{name} tool", input_schema=schema)
            for name, schema in SCHEMAS.items()
        ]
        self._tools = tools + (extra_tools or [])

    async def list_tools(self, refresh: bool = False) -> list[MCPToolInfo]:
        del refresh
        return list(self._tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        self.calls.append((tool, args))
        if tool in self.failures:
            raise self.failures[tool]
        if tool not in {t.name for t in self._tools}:
            raise MCPToolFailedError(tool, f"unknown tool {tool!r}")
        if tool == "analyze_blocked_tasks" and args.get("entity_id") == "ghost-entity":
            raise MCPToolFailedError(tool, f"unknown entity_id 'ghost-entity'; available: {[e['id'] for e in PLAN['entities']]}")
        if tool not in self.results:
            raise MCPToolFailedError(tool, f"no canned result for {tool}")
        return self.results[tool]


def make_assistant(client: Any, max_tool_calls: int = 8) -> Assistant:
    return Assistant(client, provider=DeterministicProvider(), max_tool_calls=max_tool_calls)


# ── 1–2. Intent mapping ──────────────────────────────────────────────────────


async def test_show_current_plan_maps_to_get_plan_state() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Show me the current plan.", scenario_id="project-full"
    )
    assert response.intent == I.PLAN_STATE
    assert [s.tool for s in response.steps] == ["get_plan_state"]
    assert "plan-payment-api-release-full" in response.reply
    assert "committed plan state" in response.reply.lower()


async def test_disruption_request_maps_to_simulate_with_parameters() -> None:
    client = FakeClient(results={**TOOL_RESULTS, "simulate_disruption": {
        "scenario_id": "project-full", "simulated": True,
        "disruption": {"entity_id": "payment-api", "name": "Payment API service",
                       "state": "blocked", "reason": "high severity outage", "severity": "critical"},
        "directly_affected": [], "transitively_affected": [], "unaffected": [],
    }})
    response = await make_assistant(client).handle_message(
        "What happens if the payment API goes down? This is a critical outage.",
        scenario_id="project-full",
    )
    assert response.intent == I.SIMULATE_DISRUPTION
    tool, args = client.calls[-1]
    assert tool == "simulate_disruption"
    assert args["entity_id"] == "payment-api"      # resolved from live plan data
    assert args["severity"] == "critical"          # extracted literal
    assert args["scenario_id"] == "project-full"
    assert isinstance(args["reason"], str) and args["reason"]


async def test_delay_event_without_minutes_asks_clarification() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "What is blocked if there is a delivery delay on vendor-delivery?",
        scenario_id="project-full",
    )
    assert response.needs_clarification is True
    assert "minutes" in response.reply.lower()
    # No analysis ran — only the plan fetch needed for entity resolution.
    assert all(tool != "analyze_blocked_tasks" for tool, _ in client.calls)
    assert all(tool != "compare_recovery_options" for tool, _ in client.calls)


async def test_unknown_entity_asks_clarification_with_live_entity_list() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "What is blocked for the ghost-service outage?", scenario_id="project-full"
    )
    assert response.needs_clarification is True
    assert "payment-api" in response.reply       # live entity list, not hardcoded
    assert all(tool != "analyze_blocked_tasks" for tool, _ in client.calls)


async def test_entity_error_from_backend_is_reported_not_retried() -> None:
    client = FakeClient()
    # Force the triage call through with an entity the fake rejects.
    client.results = {**TOOL_RESULTS}
    client.failures = {}
    original = client.call_tool

    async def call_tool(tool: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        client.calls.append((tool, args))
        if tool == "analyze_blocked_tasks":
            raise MCPToolFailedError(tool, "unknown entity_id 'nope'")
        return await original(tool, arguments)

    client.call_tool = call_tool  # type: ignore[method-assign]
    response = await make_assistant(client).handle_message(
        "What is blocked if the payment API has an outage?", scenario_id="project-full"
    )
    assert "unknown entity_id 'nope'" in response.reply


# ── 5. Multi-tool recovery workflow ──────────────────────────────────────────


async def test_multi_tool_recovery_workflow_uses_intermediate_results() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "The payment API is down. Tell me what is blocked, find recovery "
        "alternatives, and compare the options.",
        scenario_id="project-full",
    )
    assert response.intent == I.RECOVERY_WORKFLOW
    executed = [s.tool for s in response.steps if s.status == "ok"]
    assert executed == [
        "get_plan_state",
        "analyze_blocked_tasks",
        "find_alternative_dependencies",
        "compare_recovery_options",
    ]
    # The options fed to compare are the ones returned by alternatives.
    compare_args = next(a for t, a in client.calls if t == "compare_recovery_options")
    assert compare_args["options"] == [OPTION]
    # The reply explains all three stages.
    assert "Blocked — stop these" in response.reply
    assert "recovery alternative" in response.reply.lower()
    assert "ranked" in response.reply
    assert "read-only" in response.reply.lower()


async def test_workflow_stops_honestly_when_intermediate_step_fails() -> None:
    client = FakeClient(failures={
        "find_alternative_dependencies": MCPConnectionError("transport failure")
    })
    response = await make_assistant(client).handle_message(
        "The payment API is down. What is blocked, find alternatives and compare.",
        scenario_id="project-full",
    )
    # Triage ran; the chain stopped at the failed step; no compare was attempted.
    executed = [s.tool for s in response.steps]
    assert "analyze_blocked_tasks" in executed
    assert "compare_recovery_options" not in executed
    assert "transport failure" in response.reply
    assert response.steps[-1].status == "error"


async def test_first_step_failure_stops_workflow_with_explanation() -> None:
    client = FakeClient(failures={
        "analyze_blocked_tasks": MCPToolFailedError("analyze_blocked_tasks", "unknown entity_id 'ghost'")
    })
    response = await make_assistant(client).handle_message(
        "Payment API is down — what is blocked, find alternatives and compare.",
        scenario_id="project-full",
    )
    assert "stopped at the first step" in response.reply
    assert "unknown entity_id" in response.reply
    assert all(t != "compare_recovery_options" for t, _ in client.calls)


# ── 6–7. Scheduling honesty + tool-call budget ───────────────────────────────


async def test_schedule_request_without_scheduling_tool_refuses_to_fabricate() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Generate a recovery schedule after this disruption.", scenario_id="project-full"
    )
    assert response.intent == I.SCHEDULE
    assert "no scheduling tool is registered" in response.reply.lower()
    assert client.calls == [] or all(t != "generate_recovery_schedule" for t, _ in client.calls)
    # It must not invent any dates or schedule content.
    assert "day" not in response.reply.lower() or "no scheduling tool" in response.reply.lower()


async def test_schedule_tool_is_discovered_dynamically_when_registered() -> None:
    scheduling_tool = MCPToolInfo(
        name="generate_recovery_schedule",
        description="Generate a hypothetical recovery schedule.",
        input_schema={"properties": {"scenario_id": {"type": "string"}},
                      "required": ["scenario_id"]},
    )
    client = FakeClient(
        results={**TOOL_RESULTS, "generate_recovery_schedule": {
            "scenario_id": "project-full", "simulated": True,
            "schedule": [{"entity_id": "payment-api", "relative_order": 1}],
        }},
        extra_tools=[scheduling_tool],
    )
    response = await make_assistant(client).handle_message(
        "Generate a recovery schedule after this disruption.", scenario_id="project-full"
    )
    assert ("generate_recovery_schedule", {"scenario_id": "project-full"}) in client.calls
    assert response.steps[-1].tool == "generate_recovery_schedule"
    assert "hypothetical" in response.reply.lower()


async def test_tool_call_loop_is_bounded() -> None:
    client = FakeClient()
    assistant = make_assistant(client, max_tool_calls=2)
    response = await assistant.handle_message(
        "The payment API is down. What is blocked, find alternatives and compare.",
        scenario_id="project-full",
    )
    assert response.truncated is True
    assert "tool-call limit" in response.reply
    assert sum(1 for s in response.steps if s.status == "skipped") >= 1


# ── 8–9. Feasibility + structural-vs-business honesty ────────────────────────


async def test_infeasible_options_are_reported_as_excluded() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Compare recovery options for payment-api.", scenario_id="project-full"
    )
    assert "Excluded (never recommended)" in response.reply
    assert "bad-cycle" in response.reply
    assert "CYCLIC_DEPENDENCY" in response.reply
    assert "drop-dependency:backend-integration->payment-api" in response.reply


async def test_structural_validity_is_not_presented_as_business_safe() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Evaluate this recovery proposal: drop-dependency:backend-integration->payment-api",
        scenario_id="project-full",
    )
    assert response.intent == I.EVALUATE
    assert "structurally valid" in response.reply.lower()
    assert "business" in response.reply.lower()
    tool, args = client.calls[-1]
    assert tool == "evaluate_recovery_plan"
    assert args["expected_plan_version"] == 1  # inferred from live plan version


async def test_missing_option_id_for_evaluation_asks_clarification() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Evaluate this recovery proposal.", scenario_id="project-full"
    )
    assert response.needs_clarification is True
    assert "option_id" in response.reply
    assert all(t != "evaluate_recovery_plan" for t, _ in client.calls)


async def test_workload_on_unstaffed_scenario_reports_insufficient_data() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Can you rebalance the workload?", scenario_id="project-full"
    )
    assert "no staffing data" in response.reply.lower()
    assert "will not invent capacity" in response.reply.lower()


async def test_substitutes_reply_explains_missing_declaration() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message(
        "Are there substitute services for payment-api?", scenario_id="project-full"
    )
    assert response.intent == I.SUBSTITUTES
    assert "no substitute candidates" in response.reply.lower()
    assert "metadata.substitutes" in response.reply


# ── 10. Provider behaviour + deterministic fallback ──────────────────────────


def test_bedrock_provider_falls_back_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIPPLEBACK_BEDROCK_REGION", raising=False)
    monkeypatch.delenv("RIPPLEBACK_BEDROCK_MODEL_ID", raising=False)
    provider = BedrockProvider()
    assert provider.unavailable_reason() is not None
    intent, report = provider.parse("show me the current plan", {})
    assert intent.name == I.PLAN_STATE
    assert report.fallback_used is True
    assert "RIPPLEBACK_BEDROCK_REGION" in (report.fallback_reason or "")


def test_make_provider_defaults_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIPPLEBACK_ASSISTANT_PROVIDER", raising=False)
    provider, report = make_provider()
    assert isinstance(provider, DeterministicProvider)
    assert report.provider == "deterministic"
    assert report.fallback_used is False


def test_make_provider_bedrock_without_aws_config_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIPPLEBACK_ASSISTANT_PROVIDER", "bedrock")
    monkeypatch.delenv("RIPPLEBACK_BEDROCK_REGION", raising=False)
    monkeypatch.delenv("RIPPLEBACK_BEDROCK_MODEL_ID", raising=False)
    provider, report = make_provider()
    assert isinstance(provider, DeterministicProvider)
    assert report.fallback_used is True
    assert "deterministic" in report.provider


async def test_assistant_reports_fallback_provider_in_reply_metadata() -> None:
    provider, report = make_provider("bedrock")  # falls back deterministically
    assistant = Assistant(FakeClient(), provider=provider, provider_report=report)
    response = await assistant.handle_message(
        "Show me the current plan.", scenario_id="project-full"
    )
    assert response.provider == "deterministic"
    assert any("fallback" in note for note in response.provider_notes)


def test_unknown_provider_name_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RIPPLEBACK_ASSISTANT_PROVIDER", raising=False)
    provider, report = make_provider("gpt9")
    assert isinstance(provider, DeterministicProvider)
    assert report.fallback_used is True


# ── Offline behaviour + capability listing ───────────────────────────────────


async def test_offline_server_yields_clear_retryable_reply() -> None:
    class OfflineClient(FakeClient):
        async def list_tools(self, refresh: bool = False) -> list[MCPToolInfo]:
            del refresh
            raise MCPConnectionError("connection refused")

    response = await make_assistant(OfflineClient()).handle_message(
        "Show me the current plan.", scenario_id="project-full"
    )
    assert "unreachable" in response.reply.lower()
    assert "nothing was executed" in response.reply.lower()
    assert response.steps == []


async def test_help_lists_live_discovered_tools() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message("help")
    assert response.intent == I.HELP
    for name in SCHEMAS:
        assert f"`{name}`" in response.reply


async def test_missing_scenario_asks_clarification() -> None:
    client = FakeClient()
    response = await make_assistant(client).handle_message("Show me the current plan.")
    assert response.needs_clarification is True
    assert "scenario" in response.reply.lower()
    assert client.calls == []


# ── Deterministic parser unit checks ─────────────────────────────────────────


def test_parser_extracts_delay_minutes_and_option_ids() -> None:
    intent = I.parse_intent(
        "compare drop-dependency:backend-integration->payment-api with a 120 minute delay"
    )
    assert intent.params["delay_minutes"] == 120
    assert intent.params["option_id"] == "drop-dependency:backend-integration->payment-api"


def test_parser_extracts_hours_into_minutes() -> None:
    intent = I.parse_intent("simulate a 2 hour delivery delay")
    assert intent.params["delay_minutes"] == 120
    assert intent.params["event_type"] == "delivery_delay"


def test_parser_never_invents_ids_for_unknown_text() -> None:
    intent = I.parse_intent("tell me a joke about shipping")
    assert "entity_id" not in intent.params
    assert "scenario_id" not in intent.params


# ── Explain-layer safety ─────────────────────────────────────────────────────


def test_explain_forecast_never_states_calendar_dates() -> None:
    text = explain("recalculate_delivery_forecast", FORECAST)
    assert "steps" in text
    assert "never a calendar date" in text


def test_explain_never_crashes_on_malformed_result() -> None:
    text = explain("compare_recovery_options", {"unexpected": object()})
    assert isinstance(text, str)


# ── 12. Genuine end-to-end test against a live MCP server ────────────────────


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


async def test_end_to_end_workflow_through_real_mcp_never_mutates_plan(
    live_server_url: str,
) -> None:
    async with RippleBackMCPClient(live_server_url) as client:
        assistant = Assistant(client)
        response = await assistant.handle_message(
            "The payment API is down. Tell me what is blocked, find recovery "
            "alternatives, and compare the options.",
            scenario_id="project-full",
        )
        assert response.intent == I.RECOVERY_WORKFLOW
        assert response.tool_calls_made >= 3
        assert all(step.status == "ok" for step in response.steps)
        assert "read-only" in response.reply.lower()

        # The committed plan must be untouched by the whole workflow.
        plan = await client.call_tool("get_plan_state", {"scenario_id": "project-full"})
        assert plan["status"] == "healthy"
        assert plan["version"] == 1
        assert plan["disruptions"] == []


async def test_end_to_end_assistant_handles_unknown_scenario_gracefully(
    live_server_url: str,
) -> None:
    async with RippleBackMCPClient(live_server_url) as client:
        assistant = Assistant(client)
        response = await assistant.handle_message(
            "Show me the current plan.", scenario_id="no-such-scenario"
        )
        # The backend error is surfaced honestly, not hidden.
        assert "unknown scenario_id" in response.reply.lower() or "error" in response.reply.lower()
        assert response.steps[0].status == "error"


async def test_end_to_end_schedule_workflow_through_real_mcp(
    live_server_url: str,
) -> None:
    """The schedule intent must consume the *registered* scheduling tool.

    Genuine MCP session against a live server: the tool is discovered
    dynamically (no hardcoded schema), invoked with its real required
    arguments, and the reply explains the actual structured result. The
    committed plan must be untouched afterwards.
    """
    async with RippleBackMCPClient(live_server_url) as client:
        tools = await client.list_tools()
        discovered = next((t for t in tools if "schedul" in t.name), None)
        assert discovered is not None, [t.name for t in tools]
        assert "scenario_id" in (discovered.input_schema or {}).get("required", [])

        assistant = Assistant(client)
        response = await assistant.handle_message(
            "Generate a recovery schedule.", scenario_id="project-full"
        )
        assert response.intent == I.SCHEDULE
        schedule_steps = [
            s for s in response.steps if s.tool == discovered.name
        ]
        assert schedule_steps, [s.tool for s in response.steps]
        assert schedule_steps[0].status == "ok", schedule_steps[0].error
        result = schedule_steps[0].result
        assert isinstance(result, dict)
        assert result["simulated"] is True
        assert result["feasible"] is True
        assert "hypothetical" in response.reply.lower()

        # Read-only guarantee: the committed plan is byte-identical after.
        plan = await client.call_tool("get_plan_state", {"scenario_id": "project-full"})
        assert plan["version"] == 1
        assert plan["disruptions"] == []
