"""Unit tests for substitute-service recovery options (Person 3, Milestone 5)."""

from __future__ import annotations

import copy

import pytest

from rippleback.comparison import compare_recovery_options
from rippleback.constraints import ConstraintError, validate_hard_constraints
from rippleback.forecast import recalculate_delivery_forecast
from rippleback.recovery import (
    RecoveryError,
    find_alternative_dependencies,
    find_substitute_options,
)
from rippleback.scenarios import SCENARIOS, get_scenario

REPORT_KEYS = [
    "scenario_id",
    "plan_id",
    "plan_version",
    "simulated",
    "disruption",
    "outcome",
    "options",
    "missing_data",
    "assumptions",
    "limitations",
]
OPTION_KEYS = [
    "option_id",
    "explanation",
    "disrupted_entity_id",
    "substitute_entity_id",
    "affected_entity_ids",
    "affected_dependencies",
    "changes",
    "feasible",
    "reasons",
    "violations",
    "assumptions",
    "limitations",
]


def _scenario(
    *,
    substitutes: object = ("backup-core",),
    extra_entities: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    constraints: list[dict] | None = None,
) -> dict:
    """Synthetic scenario where ``core`` declares substitute candidates."""
    core_entity: dict = {
        "id": "core",
        "name": "Core",
        "status": "in_progress",
    }
    if substitutes is not None:
        core_entity["metadata"] = {
            "substitutes": substitutes if isinstance(substitutes, str) else list(substitutes)
        }
    entities = [
        core_entity,
        {"id": "backup-core", "name": "Backup core", "status": "pending"},
        {"id": "alpha", "name": "Alpha", "status": "pending"},
        {"id": "beta", "name": "Beta", "status": "pending"},
    ]
    if extra_entities:
        entities.extend(copy.deepcopy(extra_entities))
    return {
        "scenario_id": "synthetic-substitute",
        "plan_id": "plan-synthetic-substitute",
        "version": 4,
        "status": "healthy",
        "entities": entities,
        "dependencies": dependencies
        if dependencies is not None
        else [
            {"dependent": "alpha", "prerequisite": "core"},
            {"dependent": "beta", "prerequisite": "core"},
        ],
        "constraints": constraints if constraints is not None else [],
    }


def _cycle_scenario() -> dict:
    """Substituting ``gamma`` for ``core`` makes alpha require gamma cyclically."""
    return _scenario(
        substitutes=("gamma",),
        extra_entities=[{"id": "gamma", "name": "Gamma", "status": "pending"}],
        dependencies=[
            {"dependent": "alpha", "prerequisite": "core"},
            {"dependent": "gamma", "prerequisite": "alpha"},
        ],
    )


def _gate_violated_scenario() -> dict:
    scenario = _scenario()
    for entity in scenario["entities"]:
        if entity["id"] == "alpha":
            entity["status"] = "done"
    scenario["constraints"] = [
        {"type": "all_entities_done_before", "entity_ids": ["alpha"]}
    ]
    return scenario


class TestValidSubstitute:
    def test_declared_substitute_yields_feasible_option(self) -> None:
        result = find_substitute_options(_scenario(), entity_id="core")
        assert result["outcome"] == "substitutes_proposed"
        assert result["missing_data"] == []
        assert len(result["options"]) == 1
        option = result["options"][0]
        assert option["option_id"] == "substitute:core->backup-core"
        assert option["feasible"] is True
        assert option["reasons"] == []
        assert option["violations"] == []
        assert option["disrupted_entity_id"] == "core"
        assert option["substitute_entity_id"] == "backup-core"
        assert option["affected_entity_ids"] == ["alpha", "beta"]
        assert option["explanation"]

    def test_change_set_reroutes_only_the_disrupted_edges(self) -> None:
        result = find_substitute_options(_scenario(), entity_id="core")
        changes = result["options"][0]["changes"]
        assert changes == [
            {
                "action": "remove_dependency",
                "dependent": "alpha",
                "prerequisite": "core",
            },
            {
                "action": "add_dependency",
                "dependent": "alpha",
                "prerequisite": "backup-core",
            },
            {
                "action": "remove_dependency",
                "dependent": "beta",
                "prerequisite": "core",
            },
            {
                "action": "add_dependency",
                "dependent": "beta",
                "prerequisite": "backup-core",
            },
        ]
        assert option_ids_and_affected_dependencies_unchanged(result)

    def test_change_set_passes_the_hard_constraint_gate(self) -> None:
        scenario = _scenario()
        option = find_substitute_options(scenario, entity_id="core")["options"][0]
        gate = validate_hard_constraints(scenario, changes=option["changes"])
        assert gate["feasible"] is True
        assert option["feasible"] is gate["feasible"]

    def test_dependency_type_and_parameters_are_preserved(self) -> None:
        scenario = _scenario(
            dependencies=[
                {
                    "dependent": "alpha",
                    "prerequisite": "core",
                    "dependency_type": "benefits_from",
                },
                {
                    "dependent": "beta",
                    "prerequisite": "core",
                    "dependency_type": "requires_arrival_by",
                    "constraint_parameters": {"required_buffer_minutes": 30},
                },
            ]
        )
        option = find_substitute_options(scenario, entity_id="core")["options"][0]
        assert option["changes"][1] == {
            "action": "add_dependency",
            "dependent": "alpha",
            "prerequisite": "backup-core",
            "dependency_type": "benefits_from",
        }
        assert option["changes"][3] == {
            "action": "add_dependency",
            "dependent": "beta",
            "prerequisite": "backup-core",
            "dependency_type": "requires_arrival_by",
            "constraint_parameters": {"required_buffer_minutes": 30},
        }
        assert option["feasible"] is True

    def test_report_and_option_key_order_is_stable(self) -> None:
        result = find_substitute_options(_scenario(), entity_id="core")
        assert list(result) == REPORT_KEYS
        assert list(result["options"][0]) == OPTION_KEYS
        assert result["simulated"] is True
        assert result["scenario_id"] == "synthetic-substitute"
        assert result["plan_version"] == 4
        assert result["disruption"] == {"entity_id": "core", "name": "Core"}

    def test_unique_and_stable_option_ids(self) -> None:
        result = find_substitute_options(
            _scenario(substitutes=("backup-core", "secondary-core")),
            entity_id="core",
        )
        ids = [option["option_id"] for option in result["options"]]
        assert ids == [
            "substitute:core->backup-core",
            "substitute:core->secondary-core",
        ]
        assert len(ids) == len(set(ids))
        assert all(identifier.startswith("substitute:") for identifier in ids)
        assert all(not identifier.startswith("drop-dependency:") for identifier in ids)

    def test_duplicate_declarations_are_deduplicated(self) -> None:
        result = find_substitute_options(
            _scenario(substitutes=("backup-core", "backup-core")),
            entity_id="core",
        )
        assert len(result["options"]) == 1


def option_ids_and_affected_dependencies_unchanged(result: dict) -> bool:
    """Sanity: reroute touches only edges on the disrupted prerequisite."""
    for option in result["options"]:
        assert all(
            dependency["prerequisite"] == option["disrupted_entity_id"]
            for dependency in option["affected_dependencies"]
        )
        assert all(
            change.get("prerequisite") in (
                option["disrupted_entity_id"],
                option["substitute_entity_id"],
            )
            for change in option["changes"]
        )
    return True


class TestNoCandidateResults:
    def test_every_shipped_fixture_reports_no_candidates(self) -> None:
        for scenario_id in sorted(SCENARIOS):
            scenario = get_scenario(scenario_id)
            for entity in scenario["entities"]:
                result = find_substitute_options(
                    scenario, entity_id=entity["id"]
                )
                assert result["outcome"] == "no_substitute_candidates", (
                    scenario_id,
                    entity["id"],
                )
                assert result["options"] == []
                assert result["missing_data"], (scenario_id, entity["id"])

    def test_missing_metadata_names_the_absent_field(self) -> None:
        result = find_substitute_options(
            get_scenario("demo-release-plan"), entity_id="payment-api"
        )
        assert result["outcome"] == "no_substitute_candidates"
        assert result["options"] == []
        assert len(result["missing_data"]) == 1
        assert "metadata.substitutes" in result["missing_data"][0]
        assert "payment-api" in result["missing_data"][0]

    def test_entity_without_dependents_reports_both_reasons(self) -> None:
        result = find_substitute_options(
            get_scenario("demo-release-plan"), entity_id="production-release"
        )
        assert result["outcome"] == "no_substitute_candidates"
        assert len(result["missing_data"]) == 2
        assert "metadata.substitutes" in result["missing_data"][0]
        assert "no dependency edge" in result["missing_data"][1]

    def test_empty_substitutes_list_reports_missing_data(self) -> None:
        result = find_substitute_options(_scenario(substitutes=()), entity_id="core")
        assert result["outcome"] == "no_substitute_candidates"
        assert result["options"] == []
        assert len(result["missing_data"]) == 1
        assert "empty" in result["missing_data"][0]

    def test_report_states_the_honesty_policy(self) -> None:
        result = find_substitute_options(
            get_scenario("demo-release-plan"), entity_id="payment-api"
        )
        assert any(
            "does not yet recognize the key" in limit
            for limit in result["limitations"]
        )
        assert any(
            "explicitly declares 'metadata.substitutes'" in assumption
            for assumption in result["assumptions"]
        )


class TestMalformedRequests:
    def test_unknown_entity_raises(self) -> None:
        with pytest.raises(RecoveryError, match="unknown entity_id"):
            find_substitute_options(_scenario(), entity_id="ghost")

    def test_substitutes_not_a_list_raises(self) -> None:
        with pytest.raises(RecoveryError, match="must be a list"):
            find_substitute_options(
                _scenario(substitutes="backup-core"), entity_id="core"
            )

    def test_substitutes_non_string_entry_raises(self) -> None:
        with pytest.raises(RecoveryError, match="must be a string"):
            find_substitute_options(
                _scenario(substitutes=("backup-core", 5)), entity_id="core"
            )

    def test_malformed_scenario_raises_constraint_error(self) -> None:
        scenario = _scenario()
        scenario.pop("version")
        with pytest.raises(ConstraintError, match="malformed scenario"):
            find_substitute_options(scenario, entity_id="core")


class TestRejectionsAndFeasibility:
    def test_unknown_substitute_id_is_infeasible(self) -> None:
        result = find_substitute_options(
            _scenario(substitutes=("ghost",)), entity_id="core"
        )
        assert result["outcome"] == "no_feasible_substitute"
        option = result["options"][0]
        assert option["feasible"] is False
        assert any(
            "does not name an existing entity" in reason
            for reason in option["reasons"]
        )
        assert option["violations"], "gate must report the unknown entity"
        assert all(
            violation["code"] == "ENTITY_NOT_FOUND"
            for violation in option["violations"]
        )
        assert len(option["violations"]) == len(option["changes"]) // 2
        assert any(
            "must not be adopted" in limit for limit in option["limitations"]
        )

    def test_cycle_candidate_is_not_marked_feasible(self) -> None:
        result = find_substitute_options(_cycle_scenario(), entity_id="core")
        assert result["outcome"] == "no_feasible_substitute"
        option = result["options"][0]
        assert option["option_id"] == "substitute:core->gamma"
        assert option["feasible"] is False
        assert [violation["code"] for violation in option["violations"]] == [
            "CYCLIC_DEPENDENCY"
        ]
        assert any("fails hard-constraint" in reason for reason in option["reasons"])

    def test_self_substitution_is_rejected(self) -> None:
        result = find_substitute_options(_scenario(substitutes=("core",)), entity_id="core")
        assert result["outcome"] == "no_feasible_substitute"
        option = result["options"][0]
        assert option["feasible"] is False
        assert any(
            "is not a substitution" in reason for reason in option["reasons"]
        )

    def test_substitute_equal_to_dependent_is_invalid(self) -> None:
        result = find_substitute_options(_scenario(substitutes=("alpha",)), entity_id="core")
        assert result["outcome"] == "no_feasible_substitute"
        option = result["options"][0]
        assert option["feasible"] is False
        assert [violation["code"] for violation in option["violations"]] == [
            "INVALID_DEPENDENCY"
        ]

    def test_hard_constraint_violation_marks_every_candidate_infeasible(self) -> None:
        result = find_substitute_options(_gate_violated_scenario(), entity_id="core")
        assert result["outcome"] == "no_feasible_substitute"
        option = result["options"][0]
        assert option["feasible"] is False
        assert [violation["code"] for violation in option["violations"]] == [
            "CONSTRAINT_VIOLATED"
        ]
        assert option["reasons"]


class TestPurityAndDeterminism:
    def test_inputs_unchanged_after_call(self) -> None:
        scenario = _scenario(substitutes=("backup-core", "ghost", "core"))
        pristine = copy.deepcopy(scenario)
        find_substitute_options(scenario, entity_id="core")
        assert scenario == pristine

    def test_repeated_calls_identical(self) -> None:
        scenario = _scenario(substitutes=("backup-core", "secondary-core"))
        first = find_substitute_options(scenario, entity_id="core")
        second = find_substitute_options(scenario, entity_id="core")
        assert first == second

    def test_mutating_result_does_not_corrupt_input(self) -> None:
        scenario = _scenario()
        pristine = copy.deepcopy(scenario)
        result = find_substitute_options(scenario, entity_id="core")
        option = result["options"][0]
        option["changes"].clear()
        option["assumptions"].append("injected")
        result["missing_data"].append("injected")
        assert scenario == pristine


class TestEngineCompatibility:
    def test_feasible_option_flows_through_forecast(self) -> None:
        scenario = _scenario()
        option = find_substitute_options(scenario, entity_id="core")["options"][0]
        forecast = recalculate_delivery_forecast(scenario, changes=option["changes"])
        assert forecast["feasible"] is True
        assert forecast["delay_delta"] is not None
        assert forecast["error"] is None

    def test_feasible_option_is_ranked_by_comparison(self) -> None:
        scenario = _scenario()
        option = find_substitute_options(scenario, entity_id="core")["options"][0]
        report = compare_recovery_options(scenario, [option])
        assert report["outcome"] == "ranked"
        assert report["recommendation"] == option["option_id"]
        assert report["ranked"][0]["feasible"] is True
        assert report["ranked"][0]["forecast"]["feasible"] is True

    def test_infeasible_option_is_excluded_by_the_existing_gate(self) -> None:
        cycle_scenario = _cycle_scenario()
        option = find_substitute_options(cycle_scenario, entity_id="core")["options"][0]
        assert option["feasible"] is False
        report = compare_recovery_options(cycle_scenario, [option])
        assert report["outcome"] == "no_feasible_recovery"
        assert report["recommendation"] is None
        excluded = report["excluded"][0]
        assert excluded["option_id"] == option["option_id"]
        assert excluded["feasible"] is False
        assert [violation["code"] for violation in excluded["violations"]] == [
            "CYCLIC_DEPENDENCY"
        ]

    def test_find_alternative_dependencies_contract_unchanged(self) -> None:
        scenario = _scenario()
        result = find_alternative_dependencies(scenario, entity_id="core")
        assert [option["option_id"] for option in result["options"]] == [
            "drop-dependency:alpha->core",
            "drop-dependency:beta->core",
        ]
        assert set(result) == {
            "scenario_id",
            "plan_id",
            "plan_version",
            "simulated",
            "disruption",
            "options",
        }
        for option in result["options"]:
            assert option["alternative"]["action"] == "remove_dependency"
            assert "substitute" not in option["option_id"]
