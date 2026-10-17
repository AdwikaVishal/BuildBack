"""Tests for the seeded scenario fixture and its validation."""

from __future__ import annotations

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.scenarios import (
    SCENARIOS,
    ScenarioError,
    get_scenario,
    validate_scenario,
)
from rippleback.server import get_plan_state


class TestSeededScenario:
    def test_known_scenario_is_valid(self) -> None:
        assert validate_scenario(SCENARIOS["demo-release-plan"]) == []

    def test_expected_entities_and_ids(self) -> None:
        scenario = get_scenario("demo-release-plan")
        ids = [e["id"] for e in scenario["entities"]]
        assert ids == [
            "payment-api",
            "backend-integration",
            "integration-testing",
            "frontend-integration",
            "release-validation",
            "production-release",
        ]

    def test_payment_chain_dependencies(self) -> None:
        scenario = get_scenario("demo-release-plan")
        pairs = {(d["dependent"], d["prerequisite"]) for d in scenario["dependencies"]}
        assert pairs == {
            ("backend-integration", "payment-api"),
            ("integration-testing", "backend-integration"),
            ("release-validation", "integration-testing"),
            ("production-release", "release-validation"),
        }

    def test_frontend_independent_of_payment_chain(self) -> None:
        scenario = get_scenario("demo-release-plan")
        for dep in scenario["dependencies"]:
            assert "frontend-integration" not in dep.values()

    def test_initial_plan_is_healthy(self) -> None:
        scenario = get_scenario("demo-release-plan")
        assert scenario["status"] == "healthy"


class TestLookupErrors:
    def test_unknown_scenario_id(self) -> None:
        with pytest.raises(ScenarioError, match="unknown scenario_id"):
            get_scenario("does-not-exist")

    def test_empty_scenario_id(self) -> None:
        with pytest.raises(ScenarioError, match="non-empty"):
            get_scenario("   ")

    def test_malformed_fixture_data_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = copy.deepcopy(SCENARIOS["demo-release-plan"])
        broken["entities"][0]["status"] = "on-fire"
        monkeypatch.setitem(SCENARIOS, "broken-fixture", broken)
        with pytest.raises(ScenarioError, match="malformed scenario"):
            get_scenario("broken-fixture")

    def test_tool_propagates_unknown_id_error(self) -> None:
        with pytest.raises(ToolError, match="unknown scenario_id"):
            get_plan_state("does-not-exist")

    def test_tool_does_not_fall_back_to_another_scenario(self) -> None:
        with pytest.raises(ToolError):
            get_plan_state("demo-other-plan")


class TestToolOutputSchema:
    def test_tool_returns_required_fields(self) -> None:
        plan = get_plan_state("demo-release-plan")
        for field in (
            "scenario_id",
            "plan_id",
            "version",
            "entities",
            "dependencies",
            "constraints",
        ):
            assert field in plan, f"missing required field: {field}"

    def test_tool_matches_fixture_and_is_read_only(self) -> None:
        before = copy.deepcopy(SCENARIOS["demo-release-plan"])
        plan = get_plan_state("demo-release-plan")
        assert plan["scenario_id"] == "demo-release-plan"
        assert plan["plan_id"] == before["plan_id"]
        assert plan["version"] == before["version"]
        assert plan["entities"] == before["entities"]
        assert plan["dependencies"] == before["dependencies"]
        assert plan["constraints"] == before["constraints"]
        assert SCENARIOS["demo-release-plan"] == before

    def test_tool_output_has_no_filesystem_paths(self) -> None:
        plan = get_plan_state("demo-release-plan")
        blob = repr(plan)
        assert "/Users/" not in blob
        assert "\\" not in blob
        assert ".py" not in blob

    def test_entity_and_dependency_shapes(self) -> None:
        plan = get_plan_state("demo-release-plan")
        for entity in plan["entities"]:
            assert set(entity) == {"id", "name", "status"}
        for dep in plan["dependencies"]:
            assert set(dep) == {"dependent", "prerequisite"}

    def test_mutating_tool_output_does_not_corrupt_fixture(self) -> None:
        plan = get_plan_state("demo-release-plan")
        plan["entities"].append({"id": "injected", "name": "x", "status": "done"})
        plan["constraints"][0]["type"] = "tampered"
        fresh = get_plan_state("demo-release-plan")
        assert "injected" not in [e["id"] for e in fresh["entities"]]
        assert fresh["constraints"][0]["type"] == "all_entities_done_before"


class TestValidateScenario:
    def test_non_dict(self) -> None:
        assert validate_scenario([1, 2]) == ["scenario must be a dict, got list"]

    def test_missing_fields(self) -> None:
        errors = validate_scenario({"scenario_id": "x"})
        assert any("missing scenario fields" in e for e in errors)

    def test_unknown_dependency_reference(self) -> None:
        data = copy.deepcopy(SCENARIOS["demo-release-plan"])
        data["dependencies"].append(
            {"dependent": "ghost-entity", "prerequisite": "payment-api"}
        )
        errors = validate_scenario(data)
        assert any("unknown entity 'ghost-entity'" in e for e in errors)

    def test_self_dependency(self) -> None:
        data = copy.deepcopy(SCENARIOS["demo-release-plan"])
        data["dependencies"].append(
            {"dependent": "payment-api", "prerequisite": "payment-api"}
        )
        errors = validate_scenario(data)
        assert any("self-dependency" in e for e in errors)

    def test_duplicate_entity_id(self) -> None:
        data = copy.deepcopy(SCENARIOS["demo-release-plan"])
        data["entities"].append(dict(data["entities"][0]))
        errors = validate_scenario(data)
        assert any("duplicate entity id" in e for e in errors)

    def test_bad_status(self) -> None:
        data = copy.deepcopy(SCENARIOS["demo-release-plan"])
        data["entities"][0]["status"] = "on-fire"
        errors = validate_scenario(data)
        assert any("not in" in e for e in errors)
