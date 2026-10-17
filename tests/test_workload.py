"""Unit tests for the deterministic workload-rebalancing engine (Phase 5)."""

from __future__ import annotations

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.scenarios import SCENARIOS, get_scenario
from rippleback.server import rebalance_workload as rebalance_tool
from rippleback.workload import (
    GLOBAL_ASSUMPTIONS,
    GLOBAL_LIMITATIONS,
    WorkloadError,
    rebalance_workload,
)

STAFFED_ID = "demo-release-plan-staffed"
PLAIN_ID = "demo-release-plan"


def _staffed() -> dict:
    return get_scenario(STAFFED_ID)


def _unresolved_scenario() -> dict:
    """Alice is overloaded but Bob has no free capacity for either task."""
    return {
        "scenario_id": "no-room",
        "plan_id": "plan-no-room",
        "version": 1,
        "status": "healthy",
        "resources": [
            {"id": "alice", "name": "Alice", "capacity": 1},
            {"id": "bob", "name": "Bob", "capacity": 1},
        ],
        "entities": [
            {"id": "e1", "name": "E1", "status": "pending", "assignee": "alice", "effort": 1},
            {"id": "e2", "name": "E2", "status": "pending", "assignee": "alice", "effort": 1},
            {"id": "e3", "name": "E3", "status": "pending", "assignee": "bob", "effort": 1},
        ],
        "dependencies": [],
        "constraints": [],
    }


class TestValidSuggestion:
    def test_single_suggestion_moves_task_to_free_resource(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        assert report["outcome"] == "ok"
        assert report["simulated"] is True
        assert report["unit"] == "work_units"
        assert report["plan_version"] == 1
        assert len(report["suggestions"]) == 1
        suggestion = report["suggestions"][0]
        assert suggestion["suggestion_id"] == "rebalance:production-release->bob"
        assert suggestion["entity_id"] == "production-release"
        assert suggestion["effort"] == 1
        assert suggestion["from_resource_id"] == "alice"
        assert suggestion["to_resource_id"] == "bob"
        assert suggestion["reason"]
        assert suggestion["assumptions"] and suggestion["limitations"]

    def test_resource_summaries(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        by_id = {r["resource_id"]: r for r in report["resources"]}
        alice = by_id["alice"]
        bob = by_id["bob"]
        assert alice["capacity"] == 3
        assert alice["assigned_effort"] == 4
        assert alice["free_capacity"] == -1
        assert alice["overloaded"] is True
        assert alice["assigned_entity_ids"] == ["release-validation", "production-release"]
        assert bob["capacity"] == 3
        assert bob["assigned_effort"] == 2
        assert bob["free_capacity"] == 1
        assert bob["overloaded"] is False
        assert bob["assigned_entity_ids"] == ["frontend-integration"]

    def test_no_unresolved_when_overload_relieved(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        assert report["unresolved"] == []

    def test_top_level_report_shape(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        for field in (
            "scenario_id",
            "plan_id",
            "plan_version",
            "expected_plan_version",
            "simulated",
            "unit",
            "outcome",
            "resources",
            "suggestions",
            "unresolved",
            "assumptions",
            "limitations",
        ):
            assert field in report, f"missing field: {field}"
        assert report["scenario_id"] == STAFFED_ID
        assert report["plan_id"] == "plan-payment-api-release-staffed"
        assert report["expected_plan_version"] == 1
        assert report["assumptions"] and report["limitations"]


class TestDoneEntitiesExcluded:
    def test_done_entities_do_not_count_toward_workload(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        by_id = {r["resource_id"]: r for r in report["resources"]}
        # payment-api (2) + backend-integration (2) + integration-testing (1)
        # are done and assigned to alice; they must not count.
        assert by_id["alice"]["assigned_effort"] == 4
        assert by_id["alice"]["assigned_entity_ids"] == [
            "release-validation",
            "production-release",
        ]

    def test_done_entities_never_reassigned(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        done_ids = {
            e["id"] for e in _staffed()["entities"] if e["status"] == "done"
        }
        for suggestion in report["suggestions"]:
            assert suggestion["entity_id"] not in done_ids
        for item in report["unresolved"]:
            assert item["entity_id"] not in done_ids


class TestUnresolved:
    def test_no_destination_has_capacity(self) -> None:
        report = rebalance_workload(_unresolved_scenario(), expected_plan_version=1)
        assert report["outcome"] == "ok"
        assert report["suggestions"] == []
        unresolved_ids = {item["entity_id"] for item in report["unresolved"]}
        assert unresolved_ids == {"e1", "e2"}
        for item in report["unresolved"]:
            assert item["resource_id"] == "alice"
            assert item["effort"] == 1
            assert "free capacity" in item["reason"]


class TestInsufficientData:
    def test_plain_scenario_returns_insufficient_data(self) -> None:
        report = rebalance_workload(get_scenario(PLAIN_ID), expected_plan_version=1)
        assert report["outcome"] == "insufficient_data"
        assert report["resources"] == []
        assert report["suggestions"] == []
        assert report["unresolved"] == []
        assert report["simulated"] is True
        assert report["unit"] == "work_units"

    def test_insufficient_data_is_not_a_silent_empty_rebalance(self) -> None:
        report = rebalance_workload(get_scenario(PLAIN_ID), expected_plan_version=1)
        assert report["outcome"] != "ok"
        assert any(
            "no staffing data" in limitation for limitation in report["limitations"]
        )


class TestInvalidInputs:
    def test_unknown_scenario_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown scenario_id"):
            rebalance_tool("no-such-scenario", 1)

    def test_unknown_resource_raises(self) -> None:
        with pytest.raises(WorkloadError, match="unknown resource_id"):
            rebalance_workload(_staffed(), expected_plan_version=1, resource_id="ghost")

    def test_unknown_resource_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown resource_id"):
            rebalance_tool(STAFFED_ID, 1, "ghost")

    def test_stale_version_raises(self) -> None:
        with pytest.raises(WorkloadError, match="stale expected_plan_version"):
            rebalance_workload(_staffed(), expected_plan_version=99)

    def test_stale_version_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="stale expected_plan_version"):
            rebalance_tool(STAFFED_ID, 99)

    def test_non_integer_version_raises(self) -> None:
        with pytest.raises(WorkloadError, match="must be an integer"):
            rebalance_workload(_staffed(), expected_plan_version="1")  # type: ignore[arg-type]

    def test_bool_version_raises(self) -> None:
        with pytest.raises(WorkloadError, match="must be an integer"):
            rebalance_workload(_staffed(), expected_plan_version=True)  # type: ignore[arg-type]


class TestScoping:
    def test_scoping_to_non_overloaded_resource_has_no_suggestions(self) -> None:
        report = rebalance_workload(
            _staffed(), expected_plan_version=1, resource_id="bob"
        )
        assert report["suggestions"] == []
        assert report["unresolved"] == []

    def test_scoping_to_overloaded_resource_keeps_suggestion(self) -> None:
        report = rebalance_workload(
            _staffed(), expected_plan_version=1, resource_id="alice"
        )
        assert [s["suggestion_id"] for s in report["suggestions"]] == [
            "rebalance:production-release->bob"
        ]


class TestDeterminism:
    def test_repeated_calls_identical(self) -> None:
        first = rebalance_workload(_staffed(), expected_plan_version=1)
        second = rebalance_workload(_staffed(), expected_plan_version=1)
        assert first == second

    def test_repeated_unresolved_calls_identical(self) -> None:
        first = rebalance_workload(_unresolved_scenario(), expected_plan_version=1)
        second = rebalance_workload(_unresolved_scenario(), expected_plan_version=1)
        assert first == second


class TestNonMutation:
    def test_committed_fixture_unchanged(self) -> None:
        before = copy.deepcopy(SCENARIOS[STAFFED_ID])
        rebalance_tool(STAFFED_ID, 1)
        assert SCENARIOS[STAFFED_ID] == before

    def test_plain_fixture_still_unchanged(self) -> None:
        before = copy.deepcopy(SCENARIOS[PLAIN_ID])
        rebalance_tool(PLAIN_ID, 1)
        assert SCENARIOS[PLAIN_ID] == before

    def test_scenario_object_not_mutated(self) -> None:
        scenario = _staffed()
        before = copy.deepcopy(scenario)
        rebalance_workload(scenario, expected_plan_version=1)
        assert scenario == before

    def test_mutating_result_does_not_corrupt_input(self) -> None:
        scenario = _staffed()
        before = copy.deepcopy(scenario)
        report = rebalance_workload(scenario, expected_plan_version=1)
        report["suggestions"][0]["to_resource_id"] = "tampered"
        report["resources"][0]["capacity"] = 999
        assert scenario == before


class TestIndependentReportLists:
    """Regression: a returned report must own its assumptions/limitations lists.

    Previously the report exposed the module-level ``GLOBAL_ASSUMPTIONS`` /
    ``GLOBAL_LIMITATIONS`` lists by reference, so a caller mutating one result
    corrupted the shared constants and every later call. Identical inputs must
    always yield identical output.
    """

    def test_returned_lists_are_not_the_module_constants(self) -> None:
        report = rebalance_workload(_staffed(), expected_plan_version=1)
        assert report["assumptions"] is not GLOBAL_ASSUMPTIONS
        assert report["limitations"] is not GLOBAL_LIMITATIONS

        insufficient = rebalance_workload(
            get_scenario(PLAIN_ID), expected_plan_version=1
        )
        assert insufficient["assumptions"] is not GLOBAL_ASSUMPTIONS
        assert insufficient["limitations"] is not GLOBAL_LIMITATIONS

    def test_mutating_ok_report_does_not_corrupt_globals_or_later_calls(
        self,
    ) -> None:
        clean_assumptions = copy.deepcopy(GLOBAL_ASSUMPTIONS)
        clean_limitations = copy.deepcopy(GLOBAL_LIMITATIONS)

        first = rebalance_workload(_staffed(), expected_plan_version=1)
        first["assumptions"].append("INJECTED")
        first["limitations"].append("INJECTED")

        second = rebalance_workload(_staffed(), expected_plan_version=1)
        assert "INJECTED" not in second["assumptions"]
        assert "INJECTED" not in second["limitations"]
        assert second["assumptions"] == clean_assumptions
        assert second["limitations"] == clean_limitations

        assert GLOBAL_ASSUMPTIONS == clean_assumptions
        assert GLOBAL_LIMITATIONS == clean_limitations

    def test_mutating_insufficient_report_does_not_corrupt_globals_or_later_calls(
        self,
    ) -> None:
        clean_assumptions = copy.deepcopy(GLOBAL_ASSUMPTIONS)
        clean_limitations = copy.deepcopy(GLOBAL_LIMITATIONS)

        first = rebalance_workload(get_scenario(PLAIN_ID), expected_plan_version=1)
        first["assumptions"].append("INJECTED")
        first["limitations"].append("INJECTED")

        second = rebalance_workload(get_scenario(PLAIN_ID), expected_plan_version=1)
        assert "INJECTED" not in second["assumptions"]
        assert "INJECTED" not in second["limitations"]
        assert second["assumptions"] == clean_assumptions
        assert second["limitations"][1:] == clean_limitations

        assert GLOBAL_ASSUMPTIONS == clean_assumptions
        assert GLOBAL_LIMITATIONS == clean_limitations
