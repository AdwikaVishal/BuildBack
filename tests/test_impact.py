"""Unit tests for the deterministic disruption impact engine (Phase 2)."""

from __future__ import annotations

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.impact import ImpactError, simulate_disruption
from rippleback.scenarios import SCENARIOS, get_scenario
from rippleback.server import get_plan_state
from rippleback.server import simulate_disruption as simulate_tool

SCENARIO_ID = "demo-release-plan"
TARGET = "payment-api"


def _scenario() -> dict:
    return get_scenario(SCENARIO_ID)


def _ids(entries: list[dict]) -> list[str]:
    return [entry["entity_id"] for entry in entries]


class TestDirectImpact:
    def test_directly_affected_is_one_hop(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        assert _ids(result["directly_affected"]) == ["backend-integration"]

    def test_direct_entries_are_blocked_with_evidence(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        entry = result["directly_affected"][0]
        assert entry["state"] == "blocked"
        assert entry["evidence_path"] == ["payment-api", "backend-integration"]


class TestTransitiveImpact:
    def test_transitive_chain_order_and_membership(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        assert _ids(result["transitively_affected"]) == [
            "integration-testing",
            "release-validation",
            "production-release",
        ]

    def test_transitive_evidence_paths(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        paths = {e["entity_id"]: e["evidence_path"] for e in result["transitively_affected"]}
        assert paths["integration-testing"] == [
            "payment-api",
            "backend-integration",
            "integration-testing",
        ]
        assert paths["production-release"] == [
            "payment-api",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]

    def test_disrupting_mid_chain(self) -> None:
        result = simulate_disruption(
            _scenario(), entity_id="integration-testing", reason="outage"
        )
        assert _ids(result["directly_affected"]) == ["release-validation"]
        assert _ids(result["transitively_affected"]) == ["production-release"]
        assert _ids(result["unaffected"]) == [
            "payment-api",
            "backend-integration",
            "frontend-integration",
        ]

    def test_leaf_target_has_no_impact(self) -> None:
        result = simulate_disruption(
            _scenario(), entity_id="production-release", reason="outage"
        )
        assert result["directly_affected"] == []
        assert result["transitively_affected"] == []
        assert len(result["unaffected"]) == 5


class TestIndependentWork:
    def test_frontend_integration_not_blocked(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        unaffected = {e["entity_id"]: e["state"] for e in result["unaffected"]}
        assert unaffected.get("frontend-integration") == "in_progress"

    def test_frontend_never_in_affected_lists(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        affected = _ids(result["directly_affected"]) + _ids(result["transitively_affected"])
        assert "frontend-integration" not in affected


class TestUnknownIds:
    def test_unknown_entity_raises(self) -> None:
        with pytest.raises(ImpactError, match="unknown entity_id"):
            simulate_disruption(_scenario(), entity_id="ghost", reason="outage")

    def test_unknown_scenario_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown scenario_id"):
            simulate_tool("no-such-scenario", TARGET, "outage")

    def test_unknown_entity_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown entity_id"):
            simulate_tool(SCENARIO_ID, "ghost", "outage")

    def test_empty_reason_rejected(self) -> None:
        with pytest.raises(ImpactError, match="reason must be a non-empty string"):
            simulate_disruption(_scenario(), entity_id=TARGET, reason="   ")

    def test_bad_severity_rejected(self) -> None:
        with pytest.raises(ImpactError, match="unknown severity"):
            simulate_disruption(
                _scenario(), entity_id=TARGET, reason="outage", severity="apocalyptic"
            )


class TestImmutability:
    def test_committed_fixture_unchanged(self) -> None:
        before = copy.deepcopy(SCENARIOS[SCENARIO_ID])
        simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        assert SCENARIOS[SCENARIO_ID] == before

    def test_plan_version_not_incremented(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        assert result["plan_version"] == 1
        assert get_scenario(SCENARIO_ID)["version"] == 1

    def test_get_plan_state_still_healthy_after_simulation(self) -> None:
        simulate_tool(SCENARIO_ID, TARGET, "outage")
        plan = get_plan_state(SCENARIO_ID)
        assert plan["status"] == "healthy"
        assert plan["disruptions"] == []


class TestDeterminism:
    def test_repeated_calls_are_identical(self) -> None:
        first = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        second = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        assert first == second

    def test_default_severity_is_high(self) -> None:
        result = simulate_disruption(_scenario(), entity_id=TARGET, reason="outage")
        assert result["disruption"]["severity"] == "high"
        assert result["simulated"] is True
