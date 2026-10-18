"""Unit tests for hard-constraint validation (Person 3, Milestone 2)."""

from __future__ import annotations

import copy

import pytest

from rippleback.constraints import (
    SUPPORTED_CONSTRAINT_TYPES,
    ConstraintError,
    validate_hard_constraints,
)
from rippleback.scenarios import SCENARIOS, get_scenario

DEMO = "demo-release-plan"
FULL = "project-full"
TRAVEL = "travel"
DROP_PAYMENT = {
    "action": "remove_dependency",
    "dependent": "backend-integration",
    "prerequisite": "payment-api",
}
CYCLE_CHANGE = {
    "action": "add_dependency",
    "dependent": "payment-api",
    "prerequisite": "production-release",
}


def _set_statuses(scenario: dict, statuses: dict[str, str]) -> dict:
    for entity in scenario["entities"]:
        if entity["id"] in statuses:
            entity["status"] = statuses[entity["id"]]
    return scenario


def _all_done(scenario: dict) -> dict:
    return _set_statuses(
        scenario, {entity["id"]: "done" for entity in scenario["entities"]}
    )


class TestNoConstraints:
    def test_travel_has_no_hard_constraints(self) -> None:
        result = validate_hard_constraints(get_scenario(TRAVEL))
        assert result["scenario_id"] == TRAVEL
        assert result["constraints"] == []
        assert result["violations"] == []
        assert result["graph_errors"] == []
        assert result["feasible"] is True

    def test_report_has_stable_key_order(self) -> None:
        result = validate_hard_constraints(get_scenario(DEMO))
        assert list(result) == [
            "scenario_id",
            "plan_id",
            "plan_version",
            "simulated",
            "changes",
            "feasible",
            "graph_errors",
            "constraints",
            "violations",
            "assumptions",
            "limitations",
        ]
        assert result["simulated"] is True
        assert result["plan_version"] == 1
        assert result["changes"] == []
        assert result["assumptions"]
        assert result["limitations"]

    def test_constraint_entry_shape(self) -> None:
        result = validate_hard_constraints(get_scenario(DEMO))
        entry = result["constraints"][0]
        assert entry == {
            "index": 0,
            "type": "all_entities_done_before",
            "entity_ids": ["production-release"],
            "description": "Every other entity must be done before production-release.",
            "status": "satisfied",
        }


class TestSatisfiedConstraint:
    def test_committed_demo_is_satisfied(self) -> None:
        result = validate_hard_constraints(get_scenario(DEMO))
        assert result["constraints"][0]["status"] == "satisfied"
        assert result["violations"] == []
        assert result["feasible"] is True

    def test_committed_project_full_is_satisfied(self) -> None:
        result = validate_hard_constraints(get_scenario(FULL))
        assert result["constraints"][0]["status"] == "satisfied"
        assert result["feasible"] is True

    def test_gate_not_done_is_satisfied(self) -> None:
        scenario = _set_statuses(get_scenario(DEMO), {"production-release": "in_progress"})
        result = validate_hard_constraints(scenario)
        assert result["constraints"][0]["status"] == "satisfied"
        assert result["feasible"] is True

    def test_all_entities_done_is_satisfied(self) -> None:
        result = validate_hard_constraints(_all_done(get_scenario(DEMO)))
        assert result["constraints"][0]["status"] == "satisfied"
        assert result["violations"] == []
        assert result["feasible"] is True

    def test_unknown_constraint_type_is_unsupported_not_satisfied(self) -> None:
        scenario = get_scenario(DEMO)
        scenario["constraints"] = [
            {
                "type": "budget_cap",
                "entity_ids": ["payment-api"],
                "description": "Undocumented type.",
            }
        ]
        result = validate_hard_constraints(scenario)
        assert result["constraints"][0]["status"] == "unsupported"
        assert result["violations"][0]["code"] == "UNSUPPORTED_CONSTRAINT"
        assert "budget_cap" in result["violations"][0]["message"]
        assert result["feasible"] is False


class TestViolatedConstraint:
    def test_gate_done_while_another_entity_is_not(self) -> None:
        scenario = _set_statuses(
            get_scenario(DEMO),
            {"production-release": "done", "frontend-integration": "in_progress"},
        )
        result = validate_hard_constraints(scenario)
        assert result["feasible"] is False
        assert result["constraints"][0]["status"] == "violated"
        violation = result["violations"][0]
        assert violation["code"] == "CONSTRAINT_VIOLATED"
        assert violation["source"] == "constraint"
        assert violation["constraint_index"] == 0
        assert violation["constraint_type"] == "all_entities_done_before"
        assert violation["entity_ids"][0] == "production-release"
        assert "frontend-integration" in violation["entity_ids"]
        assert "release-validation" in violation["entity_ids"]
        assert "production-release" in violation["message"]
        assert "frontend-integration" in violation["message"]

    def test_violation_entity_ids_follow_scenario_order(self) -> None:
        scenario = _set_statuses(
            get_scenario(DEMO),
            {
                "production-release": "done",
                "release-validation": "pending",
                "frontend-integration": "in_progress",
            },
        )
        result = validate_hard_constraints(scenario)
        assert result["violations"][0]["entity_ids"] == [
            "production-release",
            "frontend-integration",
            "release-validation",
        ]

    def test_second_gate_partially_done_blocks_first_gate(self) -> None:
        scenario = _set_statuses(_all_done(get_scenario(DEMO)), {})
        scenario = _set_statuses(scenario, {"production-release": "pending"})
        scenario["constraints"] = [
            {
                "type": "all_entities_done_before",
                "entity_ids": ["release-validation", "production-release"],
            }
        ]
        result = validate_hard_constraints(scenario)
        assert result["feasible"] is False
        assert result["violations"][0]["entity_ids"] == [
            "release-validation",
            "production-release",
        ]

    def test_constraint_violation_alone_makes_report_infeasible(self) -> None:
        scenario = _set_statuses(get_scenario(FULL), {"production-release": "done"})
        result = validate_hard_constraints(scenario)
        assert result["graph_errors"] == []
        assert len(result["violations"]) == 1
        assert result["feasible"] is False


class TestMalformedConstraintInputs:
    def test_constraint_missing_entity_ids_raises(self) -> None:
        scenario = get_scenario(DEMO)
        scenario["constraints"] = [{"type": "all_entities_done_before"}]
        with pytest.raises(ConstraintError, match=r"constraints\[0\] missing fields"):
            validate_hard_constraints(scenario)

    def test_constraint_referencing_unknown_entity_raises(self) -> None:
        scenario = get_scenario(DEMO)
        scenario["constraints"] = [
            {"type": "all_entities_done_before", "entity_ids": ["ghost"]}
        ]
        with pytest.raises(ConstraintError, match="references unknown entity"):
            validate_hard_constraints(scenario)

    def test_non_string_gate_reference_raises(self) -> None:
        scenario = get_scenario(DEMO)
        scenario["constraints"] = [
            {"type": "all_entities_done_before", "entity_ids": [5]}
        ]
        with pytest.raises(ConstraintError, match="must name existing entity ids"):
            validate_hard_constraints(scenario)

    def test_scenario_missing_required_field_raises(self) -> None:
        scenario = get_scenario(DEMO)
        del scenario["dependencies"]
        with pytest.raises(ConstraintError, match="missing scenario fields"):
            validate_hard_constraints(scenario)

    def test_scenario_must_be_a_dict(self) -> None:
        with pytest.raises(ConstraintError, match="scenario must be a dict"):
            validate_hard_constraints([1, 2])  # type: ignore[arg-type]

    def test_malformed_entity_status_raises(self) -> None:
        scenario = get_scenario(DEMO)
        scenario["entities"][0]["status"] = "sleeping"
        with pytest.raises(ConstraintError, match="status"):
            validate_hard_constraints(scenario)


class TestValidChanges:
    def test_removing_an_existing_edge_is_valid(self) -> None:
        result = validate_hard_constraints(get_scenario(DEMO), changes=[DROP_PAYMENT])
        assert result["feasible"] is True
        assert result["graph_errors"] == []
        assert result["violations"] == []
        assert result["changes"] == [DROP_PAYMENT]

    def test_adding_a_dependency_between_known_entities_is_valid(self) -> None:
        result = validate_hard_constraints(
            get_scenario(DEMO),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "frontend-integration",
                    "prerequisite": "payment-api",
                    "dependency_type": "requires_arrival_by",
                }
            ],
        )
        assert result["feasible"] is True
        assert result["graph_errors"] == []

    def test_no_changes_equals_explicit_empty_changes(self) -> None:
        default = validate_hard_constraints(get_scenario(DEMO))
        explicit = validate_hard_constraints(get_scenario(DEMO), changes=[])
        assert default == explicit

    def test_change_echo_is_deep_copied(self) -> None:
        changes = [dict(DROP_PAYMENT)]
        result = validate_hard_constraints(get_scenario(DEMO), changes=changes)
        result["changes"].clear()
        assert changes == [DROP_PAYMENT]


class TestMalformedChanges:
    def test_changes_not_a_list_raises(self) -> None:
        with pytest.raises(ConstraintError, match="changes must be a list"):
            validate_hard_constraints(get_scenario(DEMO), changes="oops")  # type: ignore[arg-type]

    def test_non_dict_change_raises(self) -> None:
        with pytest.raises(ConstraintError, match=r"changes\[0\] must be a dict"):
            validate_hard_constraints(get_scenario(DEMO), changes=["oops"])

    def test_unknown_action_raises(self) -> None:
        with pytest.raises(ConstraintError, match="unknown action"):
            validate_hard_constraints(
                get_scenario(DEMO),
                changes=[
                    {
                        "action": "teleport",
                        "dependent": "backend-integration",
                        "prerequisite": "payment-api",
                    }
                ],
            )

    def test_change_missing_prerequisite_raises(self) -> None:
        with pytest.raises(ConstraintError, match="requires string"):
            validate_hard_constraints(
                get_scenario(DEMO),
                changes=[{"action": "remove_dependency", "dependent": "x"}],
            )


class TestGraphViolations:
    def test_cycle_change_is_infeasible_with_coded_violation(self) -> None:
        result = validate_hard_constraints(
            get_scenario(DEMO), changes=[CYCLE_CHANGE]
        )
        assert result["feasible"] is False
        assert result["graph_errors"][0]["code"] == "CYCLIC_DEPENDENCY"
        violation = result["violations"][0]
        assert violation["code"] == "CYCLIC_DEPENDENCY"
        assert violation["source"] == "graph"
        assert violation["constraint_index"] is None
        assert violation["constraint_type"] is None
        assert violation["entity_ids"] == []
        assert "cycle detected" in violation["message"]

    def test_change_referencing_unknown_entity_is_infeasible(self) -> None:
        result = validate_hard_constraints(
            get_scenario(DEMO),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "backend-integration",
                    "prerequisite": "ghost",
                }
            ],
        )
        assert result["feasible"] is False
        assert result["graph_errors"][0]["code"] == "ENTITY_NOT_FOUND"
        assert result["violations"][0]["code"] == "ENTITY_NOT_FOUND"

    def test_self_dependency_change_is_infeasible(self) -> None:
        result = validate_hard_constraints(
            get_scenario(DEMO),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "payment-api",
                    "prerequisite": "payment-api",
                }
            ],
        )
        assert result["feasible"] is False
        assert result["graph_errors"][0]["code"] == "INVALID_DEPENDENCY"

    def test_unknown_dependency_type_is_infeasible(self) -> None:
        result = validate_hard_constraints(
            get_scenario(DEMO),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "integration-testing",
                    "prerequisite": "frontend-integration",
                    "dependency_type": "teleports",
                }
            ],
        )
        assert result["feasible"] is False
        assert result["graph_errors"][0]["code"] == "INVALID_DEPENDENCY_TYPE"

    def test_constraints_still_evaluated_when_graph_is_invalid(self) -> None:
        result = validate_hard_constraints(
            get_scenario(DEMO), changes=[CYCLE_CHANGE]
        )
        assert result["feasible"] is False
        assert result["constraints"][0]["status"] == "satisfied"

    def test_graph_violations_precede_constraint_violations(self) -> None:
        scenario = _set_statuses(get_scenario(DEMO), {"production-release": "done"})
        result = validate_hard_constraints(scenario, changes=[CYCLE_CHANGE])
        sources = [violation["source"] for violation in result["violations"]]
        assert sources == ["graph", "constraint"]
        assert result["feasible"] is False


class TestDeterminism:
    def test_repeated_calls_identical_with_changes(self) -> None:
        first = validate_hard_constraints(get_scenario(DEMO), changes=[DROP_PAYMENT])
        second = validate_hard_constraints(get_scenario(DEMO), changes=[DROP_PAYMENT])
        assert first == second

    def test_repeated_calls_identical_on_failure(self) -> None:
        scenario = _set_statuses(get_scenario(DEMO), {"production-release": "done"})
        first = validate_hard_constraints(scenario, changes=[CYCLE_CHANGE])
        second = validate_hard_constraints(scenario, changes=[CYCLE_CHANGE])
        assert first == second


class TestNonMutation:
    def test_scenario_unchanged_after_success(self) -> None:
        before = copy.deepcopy(SCENARIOS[DEMO])
        validate_hard_constraints(SCENARIOS[DEMO], changes=[DROP_PAYMENT])
        assert SCENARIOS[DEMO] == before

    def test_scenario_unchanged_after_graph_failure(self) -> None:
        before = copy.deepcopy(SCENARIOS[DEMO])
        validate_hard_constraints(SCENARIOS[DEMO], changes=[CYCLE_CHANGE])
        assert SCENARIOS[DEMO] == before

    def test_scenario_unchanged_after_malformed_changes_error(self) -> None:
        before = copy.deepcopy(SCENARIOS[DEMO])
        with pytest.raises(ConstraintError):
            validate_hard_constraints(
                SCENARIOS[DEMO], changes=[{"action": "teleport"}]
            )
        assert SCENARIOS[DEMO] == before

    def test_scenario_unchanged_after_malformed_scenario_error(self) -> None:
        scenario = get_scenario(DEMO)
        before = copy.deepcopy(scenario)
        scenario["constraints"][0]["entity_ids"].append("ghost")
        with pytest.raises(ConstraintError):
            validate_hard_constraints(scenario)
        assert scenario["entities"] == before["entities"]
        assert scenario["dependencies"] == before["dependencies"]

    def test_mutating_result_does_not_corrupt_fixture(self) -> None:
        result = validate_hard_constraints(
            SCENARIOS[DEMO],
            changes=[DROP_PAYMENT],
        )
        result["changes"].clear()
        result["constraints"][0]["entity_ids"].clear()
        result["violations"].clear()
        result["assumptions"].clear()
        result["limitations"].clear()
        fresh = get_scenario(DEMO)
        assert fresh["constraints"][0]["entity_ids"] == ["production-release"]
        assert len(fresh["dependencies"]) == 4
        assert fresh["entities"][0]["id"] == "payment-api"


class TestAssumptionsAndLimitations:
    def test_reports_state_semantics_and_scope(self) -> None:
        result = validate_hard_constraints(get_scenario(DEMO))
        joined = " ".join(result["assumptions"] + result["limitations"])
        assert "current-state invariant" in joined
        assert "statuses" in joined
        assert "no scoring" in joined.lower() or "Validation only" in joined
        assert "wall-clock" in joined

    def test_graph_failure_appends_structural_limitation(self) -> None:
        ok = validate_hard_constraints(get_scenario(DEMO), changes=[DROP_PAYMENT])
        bad = validate_hard_constraints(get_scenario(DEMO), changes=[CYCLE_CHANGE])
        assert len(bad["limitations"]) == len(ok["limitations"]) + 1
        assert any(
            "structurally invalid" in item for item in bad["limitations"]
        )

    def test_supported_types_match_documented_fixture_types(self) -> None:
        assert SUPPORTED_CONSTRAINT_TYPES == ("all_entities_done_before",)
        for scenario in SCENARIOS.values():
            for constraint in scenario["constraints"]:
                assert constraint["type"] in SUPPORTED_CONSTRAINT_TYPES
