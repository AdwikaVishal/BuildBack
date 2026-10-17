"""Unit tests for the deterministic recovery-alternatives engine (Phase 3)."""

from __future__ import annotations

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.recovery import RecoveryError, find_alternative_dependencies
from rippleback.scenarios import SCENARIOS, get_scenario
from rippleback.server import find_alternative_dependencies as recovery_tool

SCENARIO_ID = "demo-release-plan"
TARGET = "payment-api"


def _scenario() -> dict:
    return get_scenario(SCENARIO_ID)


def _synthetic_scenario(dependency_order: list[tuple[str, str]]) -> dict:
    """A minimal scenario whose dependency list uses the given order."""
    ids = ["core", "alpha", "beta", "gamma"]
    return {
        "scenario_id": "synthetic",
        "plan_id": "plan-synthetic",
        "version": 7,
        "status": "healthy",
        "entities": [
            {"id": eid, "name": eid.title(), "status": "pending"} for eid in ids
        ],
        "dependencies": [
            {"dependent": dep, "prerequisite": pre} for dep, pre in dependency_order
        ],
        "constraints": [],
    }


def _branching_scenario() -> dict:
    """Two prerequisites (alpha, beta) on one dependent (release-validation).

    ``alpha`` and ``beta`` both require ``core``; ``release-validation`` requires
    both ``alpha`` and ``beta``. Disrupting ``core`` blocks all three, but
    removing only one edge cannot free ``release-validation``.
    """
    return {
        "scenario_id": "branching",
        "plan_id": "plan-branching",
        "version": 3,
        "status": "healthy",
        "entities": [
            {"id": "core", "name": "Core", "status": "done"},
            {"id": "alpha", "name": "Alpha", "status": "pending"},
            {"id": "beta", "name": "Beta", "status": "pending"},
            {"id": "release-validation", "name": "Release validation", "status": "pending"},
        ],
        "dependencies": [
            {"dependent": "alpha", "prerequisite": "core"},
            {"dependent": "beta", "prerequisite": "core"},
            {"dependent": "release-validation", "prerequisite": "alpha"},
            {"dependent": "release-validation", "prerequisite": "beta"},
        ],
        "constraints": [],
    }


def _option(result: dict, option_id: str) -> dict:
    return next(o for o in result["options"] if o["option_id"] == option_id)


class TestValidOptions:
    def test_one_option_per_broken_dependency(self) -> None:
        result = find_alternative_dependencies(_scenario(), entity_id=TARGET)
        assert len(result["options"]) == 1
        option = result["options"][0]
        assert option["option_id"] == "drop-dependency:backend-integration->payment-api"
        assert option["affected_entity_id"] == "backend-integration"
        assert option["affected_dependency"] == {
            "dependent": "backend-integration",
            "prerequisite": "payment-api",
        }

    def test_option_has_required_fields(self) -> None:
        option = find_alternative_dependencies(_scenario(), entity_id=TARGET)["options"][0]
        for field in (
            "option_id",
            "description",
            "affected_entity_id",
            "affected_dependency",
            "alternative",
            "unblocks",
            "assumptions",
            "limitations",
        ):
            assert field in option, f"missing field: {field}"
        assert option["description"]
        assert option["assumptions"]
        assert option["limitations"]

    def test_alternative_removes_only_the_broken_edge(self) -> None:
        option = find_alternative_dependencies(_scenario(), entity_id=TARGET)["options"][0]
        alt = option["alternative"]
        assert alt["action"] == "remove_dependency"
        assert alt["removed_dependency"] == option["affected_dependency"]
        committed = _scenario()["dependencies"]
        assert alt["dependencies"] == [
            dep
            for dep in committed
            if dep != {"dependent": "backend-integration", "prerequisite": "payment-api"}
        ]
        assert alt["removed_dependency"] not in alt["dependencies"]

    def test_unblocks_full_chain(self) -> None:
        option = find_alternative_dependencies(_scenario(), entity_id=TARGET)["options"][0]
        assert option["unblocks"] == [
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]

    def test_leaf_target_has_no_options(self) -> None:
        result = find_alternative_dependencies(_scenario(), entity_id="production-release")
        assert result["options"] == []

    def test_top_level_shape(self) -> None:
        result = find_alternative_dependencies(_scenario(), entity_id=TARGET)
        assert result["scenario_id"] == SCENARIO_ID
        assert result["plan_id"] == "plan-payment-api-release"
        assert result["plan_version"] == 1
        assert result["simulated"] is True
        assert result["disruption"] == {
            "entity_id": "payment-api",
            "name": "Payment API service",
        }


class TestDeterminism:
    def test_repeated_calls_identical(self) -> None:
        first = find_alternative_dependencies(_scenario(), entity_id=TARGET)
        second = find_alternative_dependencies(_scenario(), entity_id=TARGET)
        assert first == second

    def test_option_order_follows_dependency_order(self) -> None:
        order = [("alpha", "core"), ("beta", "core")]
        result = find_alternative_dependencies(
            _synthetic_scenario(order), entity_id="core"
        )
        assert [o["affected_entity_id"] for o in result["options"]] == ["alpha", "beta"]

    def test_option_order_is_not_alphabetical(self) -> None:
        order = [("beta", "core"), ("alpha", "core")]
        result = find_alternative_dependencies(
            _synthetic_scenario(order), entity_id="core"
        )
        assert [o["affected_entity_id"] for o in result["options"]] == ["beta", "alpha"]

    def test_synthetic_plan_version_echoed(self) -> None:
        result = find_alternative_dependencies(
            _synthetic_scenario([("alpha", "core")]), entity_id="core"
        )
        assert result["plan_version"] == 7


class TestFrontendIndependence:
    def test_frontend_not_in_any_option(self) -> None:
        result = find_alternative_dependencies(_scenario(), entity_id=TARGET)
        assert result["options"], "expected at least one option for payment-api"
        for option in result["options"]:
            assert option["affected_entity_id"] != "frontend-integration"
            assert "frontend-integration" not in option["unblocks"]


class TestInvalidInputs:
    def test_unknown_entity_raises(self) -> None:
        with pytest.raises(RecoveryError, match="unknown entity_id"):
            find_alternative_dependencies(_scenario(), entity_id="ghost")

    def test_unknown_scenario_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown scenario_id"):
            recovery_tool("no-such-scenario", TARGET)

    def test_unknown_entity_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown entity_id"):
            recovery_tool(SCENARIO_ID, "ghost")

    def test_empty_entity_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown entity_id"):
            recovery_tool(SCENARIO_ID, "")


class TestNonMutation:
    def test_committed_fixture_unchanged(self) -> None:
        before = copy.deepcopy(SCENARIOS[SCENARIO_ID])
        recovery_tool(SCENARIO_ID, TARGET)
        assert SCENARIOS[SCENARIO_ID] == before

    def test_entity_statuses_and_dependencies_unchanged(self) -> None:
        before = _scenario()
        recovery_tool(SCENARIO_ID, TARGET)
        after = _scenario()
        assert after["entities"] == before["entities"]
        assert after["dependencies"] == before["dependencies"]
        assert after["version"] == before["version"] == 1

    def test_mutating_result_does_not_corrupt_fixture(self) -> None:
        result = recovery_tool(SCENARIO_ID, TARGET)
        result["options"][0]["alternative"]["dependencies"].clear()
        result["options"][0]["assumptions"].append("injected")
        assert len(_scenario()["dependencies"]) == 4


class TestUnblocksBranching:
    """`unblocks` must be computed on the residual graph, not on reachability."""

    def test_remove_alpha_does_not_unblock_release_validation(self) -> None:
        result = find_alternative_dependencies(_branching_scenario(), entity_id="core")
        option = _option(result, "drop-dependency:alpha->core")
        assert option["unblocks"] == ["alpha"]
        assert "release-validation" not in option["unblocks"]

    def test_remove_beta_does_not_unblock_release_validation(self) -> None:
        result = find_alternative_dependencies(_branching_scenario(), entity_id="core")
        option = _option(result, "drop-dependency:beta->core")
        assert option["unblocks"] == ["beta"]
        assert "release-validation" not in option["unblocks"]

    def test_release_validation_stays_blocked_in_every_option(self) -> None:
        result = find_alternative_dependencies(_branching_scenario(), entity_id="core")
        assert len(result["options"]) == 2
        for option in result["options"]:
            assert "release-validation" not in option["unblocks"]
            assert option["unblocks"] == [option["affected_entity_id"]]

    def test_dependent_with_remaining_path_is_not_unblocked(self) -> None:
        # `alpha` requires both `core` and `relay`; `relay` requires `core`.
        # Removing alpha->core leaves alpha reachable via core->relay->alpha,
        # so alpha is still blocked and must not be reported as unblocked.
        scenario = {
            "scenario_id": "residual",
            "plan_id": "plan-residual",
            "version": 1,
            "status": "healthy",
            "entities": [
                {"id": "core", "name": "Core", "status": "done"},
                {"id": "relay", "name": "Relay", "status": "pending"},
                {"id": "alpha", "name": "Alpha", "status": "pending"},
            ],
            "dependencies": [
                {"dependent": "alpha", "prerequisite": "core"},
                {"dependent": "alpha", "prerequisite": "relay"},
                {"dependent": "relay", "prerequisite": "core"},
            ],
            "constraints": [],
        }
        result = find_alternative_dependencies(scenario, entity_id="core")
        option = _option(result, "drop-dependency:alpha->core")
        assert "alpha" not in option["unblocks"]
        assert "relay" not in option["unblocks"]
        assert option["unblocks"] == []


class TestBranchingDeterminismNonMutation:
    def test_repeated_calls_identical_on_branching(self) -> None:
        first = find_alternative_dependencies(_branching_scenario(), entity_id="core")
        second = find_alternative_dependencies(_branching_scenario(), entity_id="core")
        assert first == second

    def test_option_order_on_branching_follows_dependency_order(self) -> None:
        result = find_alternative_dependencies(_branching_scenario(), entity_id="core")
        assert [o["affected_entity_id"] for o in result["options"]] == ["alpha", "beta"]

    def test_unblocks_order_is_scenario_entity_order(self) -> None:
        # Option order follows dependency order (alpha then beta); reordering
        # entities must not change it. unblocks stays consistent too.
        scenario = _branching_scenario()
        scenario["entities"] = [
            scenario["entities"][0],
            scenario["entities"][2],
            scenario["entities"][1],
            scenario["entities"][3],
        ]
        result = find_alternative_dependencies(scenario, entity_id="core")
        assert [o["affected_entity_id"] for o in result["options"]] == ["alpha", "beta"]
        assert _option(result, "drop-dependency:beta->core")["unblocks"] == ["beta"]

    def test_branching_scenario_not_mutated(self) -> None:
        scenario = _branching_scenario()
        before = copy.deepcopy(scenario)
        find_alternative_dependencies(scenario, entity_id="core")
        assert scenario == before

    def test_mutating_branching_result_does_not_corrupt_input(self) -> None:
        scenario = _branching_scenario()
        before = copy.deepcopy(scenario)
        result = find_alternative_dependencies(scenario, entity_id="core")
        result["options"][0]["alternative"]["dependencies"].clear()
        result["options"][0]["unblocks"].append("injected")
        assert scenario == before
