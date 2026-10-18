"""Unit tests for the deterministic delivery-forecast engine (Person 3, M1)."""

from __future__ import annotations

import copy

import pytest

from rippleback.critical_path import critical_path
from rippleback.forecast import ForecastError, recalculate_delivery_forecast
from rippleback.scenarios import SCENARIOS, get_scenario

PROJECT = "project-full"
DEMO = "demo-release-plan"
DROP_PAYMENT = {
    "action": "remove_dependency",
    "dependent": "backend-integration",
    "prerequisite": "payment-api",
}
OPTION = "drop-dependency:backend-integration->payment-api"


class TestBaseline:
    def test_baseline_matches_existing_engine(self) -> None:
        result = recalculate_delivery_forecast(get_scenario(PROJECT), changes=[])
        expected = critical_path(get_scenario(PROJECT))
        assert result["baseline_path"] == expected["path"]
        assert result["baseline_length"] == expected["length"]
        assert result["baseline_edges"] == expected["edges"]
        assert result["unit"] == expected["unit"] == "duration_units"

    def test_report_shape_and_echoes(self) -> None:
        result = recalculate_delivery_forecast(get_scenario(PROJECT), changes=[])
        for field in (
            "scenario_id",
            "plan_id",
            "plan_version",
            "simulated",
            "disrupted_entity_id",
            "option_id",
            "changes",
            "feasible",
            "error",
            "unit",
            "baseline_path",
            "baseline_length",
            "baseline_edges",
            "forecast_path",
            "forecast_length",
            "forecast_edges",
            "delay_delta",
            "assumptions",
            "limitations",
        ):
            assert field in result, f"missing field: {field}"
        assert result["scenario_id"] == PROJECT
        assert result["plan_id"] == "plan-payment-api-release-full"
        assert result["plan_version"] == 1
        assert result["simulated"] is True
        assert result["feasible"] is True
        assert result["error"] is None
        assert result["assumptions"]
        assert result["limitations"]

    def test_no_inputs_reports_baseline_equal_to_forecast(self) -> None:
        result = recalculate_delivery_forecast(get_scenario(PROJECT))
        assert result["changes"] == []
        assert result["feasible"] is True
        assert result["forecast_path"] == result["baseline_path"]
        assert result["forecast_length"] == result["baseline_length"]
        assert result["delay_delta"] == 0.0

    def test_delay_delta_is_forecast_minus_baseline(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT]
        )
        assert result["delay_delta"] == (
            result["forecast_length"] - result["baseline_length"]
        )


class TestRecoveryChange:
    def test_drop_payment_edge_alters_path_and_duration(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT]
        )
        assert result["feasible"] is True
        assert result["error"] is None
        assert result["baseline_path"] == [
            "payment-api",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert result["baseline_length"] == 9.0
        assert result["forecast_path"] == [
            "vendor-delivery",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert result["forecast_length"] == 8.0
        assert "payment-api" not in result["forecast_path"]
        assert result["delay_delta"] == -1.0

    def test_change_set_is_echoed(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT]
        )
        assert result["changes"] == [DROP_PAYMENT]
        assert result["option_id"] is None
        assert result["disrupted_entity_id"] is None

    def test_option_id_path_matches_explicit_changes_path(self) -> None:
        via_option = recalculate_delivery_forecast(
            get_scenario(PROJECT), option_id=OPTION
        )
        via_changes = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT],
            disrupted_entity_id="payment-api",
        )
        assert via_option["disrupted_entity_id"] == "payment-api"
        assert via_option["changes"] == [DROP_PAYMENT]
        assert via_option["option_id"] == OPTION
        for key in (
            "option_id",
            "disrupted_entity_id",
            "assumptions",
            "limitations",
        ):
            via_option.pop(key)
            via_changes.pop(key)
        assert via_option == via_changes

    def test_feasible_report_has_no_error(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT]
        )
        assert result["error"] is None
        assert result["forecast_edges"] == [
            {
                "dependent": "backend-integration",
                "prerequisite": "vendor-delivery",
                "dependency_type": "requires_completion",
            },
            {
                "dependent": "integration-testing",
                "prerequisite": "backend-integration",
                "dependency_type": "requires_completion",
            },
            {
                "dependent": "release-validation",
                "prerequisite": "integration-testing",
                "dependency_type": "requires_completion",
            },
            {
                "dependent": "production-release",
                "prerequisite": "release-validation",
                "dependency_type": "requires_completion",
            },
        ]


class TestNoOpChangeSet:
    def test_empty_change_set_is_noop(self) -> None:
        result = recalculate_delivery_forecast(get_scenario(PROJECT), changes=[])
        assert result["feasible"] is True
        assert result["delay_delta"] == 0.0
        assert result["forecast_path"] == result["baseline_path"]
        assert result["forecast_length"] == result["baseline_length"]

    def test_removing_nonexistent_edge_is_noop(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT),
            changes=[
                {
                    "action": "remove_dependency",
                    "dependent": "no-such-entity",
                    "prerequisite": "other-missing",
                }
            ],
        )
        assert result["feasible"] is True
        assert result["delay_delta"] == 0.0
        assert result["forecast_path"] == result["baseline_path"]


class TestOptionIdErrors:
    def test_malformed_option_id_raises(self) -> None:
        with pytest.raises(ForecastError, match="malformed option_id"):
            recalculate_delivery_forecast(get_scenario(PROJECT), option_id="not-an-option")

    def test_empty_option_id_raises(self) -> None:
        with pytest.raises(ForecastError, match="malformed option_id"):
            recalculate_delivery_forecast(get_scenario(PROJECT), option_id="")

    def test_option_id_with_unknown_entity_raises(self) -> None:
        with pytest.raises(ForecastError, match="unknown entity id"):
            recalculate_delivery_forecast(
                get_scenario(PROJECT), option_id="drop-dependency:ghost->payment-api"
            )

    def test_option_id_with_no_matching_edge_raises(self) -> None:
        # Reversed endpoints: both entities exist but no such committed edge.
        with pytest.raises(ForecastError, match="does not match any committed"):
            recalculate_delivery_forecast(
                get_scenario(PROJECT),
                option_id="drop-dependency:payment-api->backend-integration",
            )

    def test_option_id_prerequisite_must_match_disrupted_entity(self) -> None:
        with pytest.raises(ForecastError, match="does not match disrupted_entity_id"):
            recalculate_delivery_forecast(
                get_scenario(PROJECT),
                option_id=OPTION,
                disrupted_entity_id="vendor-delivery",
            )


class TestInvalidEntityIds:
    def test_unknown_disrupted_entity_raises(self) -> None:
        with pytest.raises(ForecastError, match="unknown disrupted_entity_id"):
            recalculate_delivery_forecast(
                get_scenario(PROJECT),
                disrupted_entity_id="ghost",
                changes=[DROP_PAYMENT],
            )

    def test_change_referencing_unknown_entity_is_infeasible(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "backend-integration",
                    "prerequisite": "ghost",
                }
            ],
        )
        assert result["feasible"] is False
        assert result["error"]["code"] == "ENTITY_NOT_FOUND"
        assert result["forecast_path"] is None
        assert result["forecast_length"] is None
        assert result["delay_delta"] is None
        assert result["baseline_path"], "baseline must still be reported"
        assert result["baseline_length"] == 9.0


class TestInvalidChangeSets:
    def test_changes_and_option_id_together_raise(self) -> None:
        with pytest.raises(ForecastError, match="not both"):
            recalculate_delivery_forecast(
                get_scenario(PROJECT), changes=[DROP_PAYMENT], option_id=OPTION
            )

    def test_changes_not_a_list_raises(self) -> None:
        with pytest.raises(ForecastError, match="changes must be a list"):
            recalculate_delivery_forecast(get_scenario(PROJECT), changes="oops")

    def test_non_dict_change_raises(self) -> None:
        with pytest.raises(ForecastError, match=r"changes\[0\] must be a dict"):
            recalculate_delivery_forecast(get_scenario(PROJECT), changes=["oops"])

    def test_unknown_action_raises(self) -> None:
        with pytest.raises(ForecastError, match="unknown action"):
            recalculate_delivery_forecast(
                get_scenario(PROJECT),
                changes=[
                    {
                        "action": "teleport",
                        "dependent": "backend-integration",
                        "prerequisite": "payment-api",
                    }
                ],
            )


class TestInfeasibleGraph:
    def test_cycle_is_infeasible_not_a_forecast(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "payment-api",
                    "prerequisite": "production-release",
                }
            ],
        )
        assert result["feasible"] is False
        assert result["error"]["code"] == "CYCLIC_DEPENDENCY"
        assert result["error"]["message"].startswith("dependency cycle detected")
        assert result["forecast_path"] is None
        assert result["forecast_length"] is None
        assert result["forecast_edges"] is None
        assert result["delay_delta"] is None
        assert result["unit"] == "duration_units"
        assert result["baseline_path"], "baseline must still be reported"
        assert result["baseline_length"] == 9.0

    def test_infeasible_report_still_carries_assumptions_and_limitations(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "payment-api",
                    "prerequisite": "production-release",
                }
            ],
        )
        assert result["assumptions"]
        assert result["limitations"]
        assert any("invalid dependency graph" in item for item in result["limitations"])

    def test_self_dependency_is_infeasible(self) -> None:
        result = recalculate_delivery_forecast(
            get_scenario(PROJECT),
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "payment-api",
                    "prerequisite": "payment-api",
                }
            ],
        )
        assert result["feasible"] is False
        assert result["error"]["code"] == "INVALID_DEPENDENCY"
        assert result["delay_delta"] is None


class TestDeterminism:
    def test_repeated_calls_identical_with_changes(self) -> None:
        first = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT]
        )
        second = recalculate_delivery_forecast(
            get_scenario(PROJECT), changes=[DROP_PAYMENT]
        )
        assert first == second

    def test_repeated_calls_identical_with_option_id(self) -> None:
        first = recalculate_delivery_forecast(get_scenario(PROJECT), option_id=OPTION)
        second = recalculate_delivery_forecast(get_scenario(PROJECT), option_id=OPTION)
        assert first == second

    def test_repeated_calls_identical_on_cycle(self) -> None:
        cycle = [
            {
                "action": "add_dependency",
                "dependent": "payment-api",
                "prerequisite": "production-release",
            }
        ]
        first = recalculate_delivery_forecast(get_scenario(PROJECT), changes=cycle)
        second = recalculate_delivery_forecast(get_scenario(PROJECT), changes=cycle)
        assert first == second


class TestNonMutation:
    def test_seeded_fixture_unchanged_after_success(self) -> None:
        before = copy.deepcopy(SCENARIOS[PROJECT])
        recalculate_delivery_forecast(SCENARIOS[PROJECT], changes=[DROP_PAYMENT])
        assert SCENARIOS[PROJECT] == before

    def test_input_scenario_unchanged_after_success(self) -> None:
        scenario = get_scenario(PROJECT)
        before = copy.deepcopy(scenario)
        recalculate_delivery_forecast(scenario, option_id=OPTION)
        assert scenario == before

    def test_input_scenario_unchanged_after_infeasible_result(self) -> None:
        scenario = get_scenario(PROJECT)
        before = copy.deepcopy(scenario)
        recalculate_delivery_forecast(
            scenario,
            changes=[
                {
                    "action": "add_dependency",
                    "dependent": "payment-api",
                    "prerequisite": "production-release",
                }
            ],
        )
        assert scenario == before

    def test_input_scenario_unchanged_after_error(self) -> None:
        scenario = get_scenario(PROJECT)
        before = copy.deepcopy(scenario)
        with pytest.raises(ForecastError):
            recalculate_delivery_forecast(scenario, option_id="not-an-option")
        assert scenario == before

    def test_mutating_result_does_not_corrupt_fixture(self) -> None:
        result = recalculate_delivery_forecast(
            SCENARIOS[PROJECT], changes=[DROP_PAYMENT]
        )
        result["baseline_path"].clear()
        result["forecast_path"].clear()
        result["changes"].clear()
        result["assumptions"].append("injected")
        fresh = get_scenario(PROJECT)
        assert len(fresh["dependencies"]) == 6
        assert fresh["entities"][0]["id"] == "auth-service"


class TestStepsFallback:
    def test_fixture_without_durations_uses_steps(self) -> None:
        result = recalculate_delivery_forecast(get_scenario(DEMO), changes=[DROP_PAYMENT])
        assert result["unit"] == "steps"
        assert result["baseline_path"] == [
            "payment-api",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert result["baseline_length"] == 5.0
        assert result["forecast_path"] == [
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert result["forecast_length"] == 4.0
        assert result["delay_delta"] == -1.0

    def test_demo_baseline_equals_existing_engine(self) -> None:
        result = recalculate_delivery_forecast(get_scenario(DEMO), changes=[])
        expected = critical_path(get_scenario(DEMO))
        assert result["baseline_length"] == expected["length"] == 5.0
        assert result["unit"] == expected["unit"] == "steps"
        assert result["delay_delta"] == 0.0
