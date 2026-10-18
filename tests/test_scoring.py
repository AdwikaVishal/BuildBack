"""Unit tests for deterministic scoring and ranking (Person 3, Milestone 3)."""

from __future__ import annotations

import copy

import pytest

from rippleback.constraints import ConstraintError, validate_hard_constraints
from rippleback.recovery import find_alternative_dependencies
from rippleback.scenarios import SCENARIOS, get_scenario
from rippleback.scoring import (
    DEFAULT_WEIGHTS,
    METRIC_KEYS,
    ScoringError,
    rank_options,
    score_option,
)

DEMO = "demo-release-plan"
FULL = "project-full"
CYCLE_CHANGES = [
    {
        "action": "add_dependency",
        "dependent": "payment-api",
        "prerequisite": "production-release",
    }
]


def _set_statuses(scenario: dict, statuses: dict[str, str]) -> dict:
    for entity in scenario["entities"]:
        if entity["id"] in statuses:
            entity["status"] = statuses[entity["id"]]
    return scenario


def _demo_payment_options() -> list[dict]:
    return find_alternative_dependencies(
        get_scenario(DEMO), entity_id="payment-api"
    )["options"]


def _full_integration_options() -> list[dict]:
    return find_alternative_dependencies(
        get_scenario(FULL), entity_id="integration-testing"
    )["options"]


class TestScoreFormula:
    def test_manually_verifiable_calculation(self) -> None:
        report = score_option(
            {"delay": -2.0, "cost": 5.0}, {"delay": 1.0, "cost": 2.0}
        )
        assert report["scorable"] is True
        assert report["score"] == 8.0
        assert report["components"] == {
            "delay": -2.0,
            "cost": 10.0,
            "risk": 0.0,
            "resource_strain": 0.0,
            "disruption_count": 0.0,
        }
        assert report["missing_metrics"] == [
            "risk",
            "resource_strain",
            "disruption_count",
        ]
        assert report["excluded_metrics"] == [
            "risk",
            "resource_strain",
            "disruption_count",
        ]
        assert report["reasons"] == []

    def test_default_weights_are_documented(self) -> None:
        assert DEFAULT_WEIGHTS == {
            "delay": 1.0,
            "cost": 0.0,
            "risk": 0.0,
            "resource_strain": 0.0,
            "disruption_count": 0.0,
        }
        assert METRIC_KEYS == (
            "delay",
            "cost",
            "risk",
            "resource_strain",
            "disruption_count",
        )

    def test_default_weights_score_delay_only(self) -> None:
        report = score_option({"delay": 3.0})
        assert report["score"] == 3.0
        assert report["weights"] == DEFAULT_WEIGHTS
        assert report["components"]["delay"] == 3.0
        assert report["components"]["cost"] == 0.0
        assert report["missing_metrics"] == [
            "cost",
            "risk",
            "resource_strain",
            "disruption_count",
        ]
        assert report["excluded_metrics"] == [
            "cost",
            "risk",
            "resource_strain",
            "disruption_count",
        ]

    def test_all_five_metrics_with_equal_weights(self) -> None:
        report = score_option(
            {
                "delay": 2.0,
                "cost": 4.0,
                "risk": 1.0,
                "resource_strain": 3.0,
                "disruption_count": 5.0,
            },
            {key: 0.5 for key in METRIC_KEYS},
        )
        assert report["score"] == 7.5
        assert report["components"] == {
            "delay": 1.0,
            "cost": 2.0,
            "risk": 0.5,
            "resource_strain": 1.5,
            "disruption_count": 2.5,
        }
        assert report["missing_metrics"] == []
        assert report["excluded_metrics"] == []

    def test_empty_metrics_unscorable_not_zero(self) -> None:
        report = score_option({})
        assert report["scorable"] is False
        assert report["score"] is None
        assert report["components"] is None
        assert "delay" in report["missing_metrics"]
        assert any("delay" in reason for reason in report["reasons"])
        assert any("fabricated zero" in reason for reason in report["reasons"])

    def test_explicit_none_metric_counts_as_missing(self) -> None:
        report = score_option({"delay": None})
        assert report["scorable"] is False
        assert report["score"] is None

    def test_zero_weight_with_absent_metric_is_scorable(self) -> None:
        report = score_option({"delay": 1.0}, {"cost": 0.0})
        assert report["scorable"] is True
        assert report["score"] == 1.0
        assert report["components"]["cost"] == 0.0


class TestWeightValidation:
    def test_weights_must_be_a_dict(self) -> None:
        with pytest.raises(ScoringError, match="weights must be a dict"):
            score_option({"delay": 1.0}, [1, 2])

    def test_unknown_weight_key_rejected(self) -> None:
        with pytest.raises(ScoringError, match="unknown weight metric"):
            score_option({"delay": 1.0}, {"speed": 1.0})

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ScoringError, match="must be non-negative"):
            score_option({"delay": 1.0}, {"delay": -1.0})

    def test_non_finite_weights_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ScoringError, match="must be finite"):
                score_option({"delay": 1.0}, {"delay": bad})

    def test_non_number_and_bool_weights_rejected(self) -> None:
        for bad in ("1", True, None):
            with pytest.raises(ScoringError, match="must be a number"):
                score_option({"delay": 1.0}, {"delay": bad})

    def test_partial_weights_merge_over_defaults(self) -> None:
        report = score_option({"delay": 1.0, "cost": 2.0}, {"cost": 3.0})
        assert report["weights"] == {**DEFAULT_WEIGHTS, "cost": 3.0}
        assert report["score"] == 7.0


class TestMetricValidation:
    def test_metrics_must_be_a_dict(self) -> None:
        with pytest.raises(ScoringError, match="metrics must be a dict"):
            score_option([1, 2])

    def test_unknown_metric_key_rejected(self) -> None:
        with pytest.raises(ScoringError, match="unknown metric"):
            score_option({"speed": 1.0})

    def test_non_finite_metrics_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ScoringError, match="must be finite"):
                score_option({"delay": bad})
            with pytest.raises(ScoringError, match="must be finite"):
                score_option({"cost": bad})

    def test_non_number_and_bool_metrics_rejected(self) -> None:
        for bad in ("1", True, None):
            if bad is None:
                continue  # explicit None means "missing", handled elsewhere
            for key in METRIC_KEYS:
                with pytest.raises(ScoringError, match="must be a number"):
                    score_option({key: bad})

    def test_negative_non_negative_metrics_rejected(self) -> None:
        for key in ("cost", "risk", "resource_strain", "disruption_count"):
            with pytest.raises(ScoringError, match="must be non-negative"):
                score_option({key: -1.0})

    def test_negative_delay_allowed_and_signed(self) -> None:
        report = score_option({"delay": -1.5})
        assert report["scorable"] is True
        assert report["score"] == -1.5


class TestRankOrdering:
    def test_lower_score_ranks_ahead_of_higher(self) -> None:
        options = [
            {"option_id": "expensive", "changes": [], "metrics": {"delay": 3.0}},
            {"option_id": "cheap", "changes": [], "metrics": {"delay": 1.0}},
        ]
        result = rank_options(get_scenario(DEMO), options)
        assert result["outcome"] == "ranked"
        assert [entry["option_id"] for entry in result["ranked"]] == [
            "cheap",
            "expensive",
        ]
        assert [entry["score"] for entry in result["ranked"]] == [1.0, 3.0]
        assert [entry["rank"] for entry in result["ranked"]] == [1, 2]
        assert result["recommendation"] == "cheap"

    def test_equal_scores_tie_break_by_option_id(self) -> None:
        options = [
            {"option_id": "option-b", "changes": [], "metrics": {"delay": 1.0}},
            {"option_id": "option-a", "changes": [], "metrics": {"delay": 1.0}},
        ]
        result = rank_options(get_scenario(DEMO), options)
        assert [entry["option_id"] for entry in result["ranked"]] == [
            "option-a",
            "option-b",
        ]
        assert [entry["rank"] for entry in result["ranked"]] == [1, 2]

    def test_custom_weights_change_the_ranking(self) -> None:
        options = [
            {"option_id": "x", "changes": [], "metrics": {"delay": 1.0, "cost": 10.0}},
            {"option_id": "y", "changes": [], "metrics": {"delay": 5.0, "cost": 0.0}},
        ]
        default = rank_options(get_scenario(DEMO), options)
        assert default["recommendation"] == "x"
        assert default["ranked"][0]["score"] == 1.0
        custom = rank_options(get_scenario(DEMO), options, {"cost": 1.0})
        assert custom["recommendation"] == "y"
        assert custom["ranked"][0]["score"] == 5.0
        assert custom["ranked"][1]["score"] == 11.0

    def test_real_fixture_recovery_options_rank_by_derived_delay(self) -> None:
        options = _full_integration_options()
        result = rank_options(get_scenario(FULL), options)
        assert result["outcome"] == "ranked"
        assert result["feasible_count"] == 2
        assert result["scorable_count"] == 2
        first, second = result["ranked"]
        assert first["option_id"] == (
            "drop-dependency:release-validation->integration-testing"
        )
        assert first["score"] == -2.0
        assert first["rank"] == 1
        assert second["option_id"] == (
            "drop-dependency:unit-testing->integration-testing"
        )
        assert second["score"] == 0.0
        assert second["rank"] == 2
        assert result["recommendation"] == first["option_id"]

    def test_real_fixture_single_option_score_is_derived(self) -> None:
        result = rank_options(get_scenario(DEMO), _demo_payment_options())
        assert result["ranked"][0]["option_id"] == (
            "drop-dependency:backend-integration->payment-api"
        )
        assert result["ranked"][0]["score"] == -1.0
        assert result["ranked"][0]["metrics"]["delay"] == -1.0


class TestDataPolicy:
    def test_missing_cost_and_risk_ok_under_default_weights(self) -> None:
        result = rank_options(get_scenario(DEMO), _demo_payment_options())
        entry = result["ranked"][0]
        assert entry["scorable"] is True
        assert "cost" in entry["missing_metrics"]
        assert "risk" in entry["missing_metrics"]
        assert result["weights"]["cost"] == 0.0
        assert result["weights"]["risk"] == 0.0

    def test_nonzero_cost_weight_without_data_is_unscorable(self) -> None:
        result = rank_options(
            get_scenario(DEMO), _demo_payment_options(), {"cost": 1.0}
        )
        assert result["outcome"] == "no_feasible_recovery"
        assert result["feasible_count"] == 1
        assert result["scorable_count"] == 0
        assert result["recommendation"] is None
        entry = result["excluded"][0]
        assert entry["feasible"] is True
        assert entry["scorable"] is False
        assert entry["score"] is None
        assert any("cost" in reason for reason in entry["reasons"])

    def test_resource_strain_derived_for_staffed_scenario(self) -> None:
        options = _demo_payment_options()
        result = rank_options(get_scenario(FULL), options, {"resource_strain": 1.0})
        entry = result["ranked"][0]
        assert entry["metrics"]["resource_strain"] == 8.0
        assert entry["components"]["resource_strain"] == 8.0
        assert entry["components"]["delay"] == -1.0
        assert entry["score"] == 7.0

    def test_resource_strain_absent_for_unstaffed_scenario(self) -> None:
        result = rank_options(
            get_scenario(DEMO), _demo_payment_options(), {"resource_strain": 1.0}
        )
        assert result["outcome"] == "no_feasible_recovery"
        entry = result["excluded"][0]
        assert entry["feasible"] is True
        assert entry["scorable"] is False
        assert any("resource_strain" in reason for reason in entry["reasons"])

    def test_caller_metrics_override_derived_values(self) -> None:
        options = [
            {"option_id": "option-a", "changes": [], "metrics": {"delay": 42.0}}
        ]
        result = rank_options(get_scenario(DEMO), options)
        assert result["ranked"][0]["score"] == 42.0
        assert result["ranked"][0]["metrics"]["delay"] == 42.0


class TestFeasibilityGate:
    def test_infeasible_options_never_recommended(self) -> None:
        scenario = _set_statuses(get_scenario(DEMO), {"production-release": "done"})
        result = rank_options(scenario, _demo_payment_options())
        assert result["outcome"] == "no_feasible_recovery"
        assert result["feasible_count"] == 0
        assert result["scorable_count"] == 0
        assert result["recommendation"] is None
        assert result["ranked"] == []
        entry = result["excluded"][0]
        assert entry["feasible"] is False
        assert entry["score"] is None
        assert entry["violations"][0]["code"] == "CONSTRAINT_VIOLATED"

    def test_cycle_option_excluded_and_violations_preserved(self) -> None:
        scenario = get_scenario(DEMO)
        options = [
            *[
                {
                    "option_id": "cycle-option",
                    "changes": CYCLE_CHANGES,
                    "metrics": {"delay": -50.0},
                }
            ],
            *_demo_payment_options(),
        ]
        result = rank_options(scenario, options)
        assert result["recommendation"] == (
            "drop-dependency:backend-integration->payment-api"
        )
        assert result["scorable_count"] == 1
        cycle = result["excluded"][0]
        assert cycle["option_id"] == "cycle-option"
        assert cycle["feasible"] is False
        assert cycle["scorable"] is False
        assert cycle["score"] is None
        assert cycle["components"] is None
        assert cycle["violations"][0]["code"] == "CYCLIC_DEPENDENCY"
        expected = validate_hard_constraints(scenario, changes=CYCLE_CHANGES)
        assert cycle["violations"] == expected["violations"]

    def test_unknown_entity_change_is_infeasible(self) -> None:
        options = [
            {
                "option_id": "ghost-edge",
                "changes": [
                    {
                        "action": "add_dependency",
                        "dependent": "backend-integration",
                        "prerequisite": "ghost",
                    }
                ],
            }
        ]
        result = rank_options(get_scenario(DEMO), options)
        assert result["outcome"] == "no_feasible_recovery"
        assert result["excluded"][0]["violations"][0]["code"] == "ENTITY_NOT_FOUND"

    def test_noop_option_id_is_excluded_not_recommended(self) -> None:
        options = [
            {
                "option_id": "drop-dependency:payment-api->backend-integration",
            },
            *_demo_payment_options(),
        ]
        result = rank_options(get_scenario(DEMO), options)
        assert result["recommendation"] == (
            "drop-dependency:backend-integration->payment-api"
        )
        noop = result["excluded"][0]
        assert noop["feasible"] is False
        assert noop["score"] is None
        assert noop["violations"] == []
        assert any("no-op" in reason for reason in noop["reasons"])


class TestNoFeasibleRecovery:
    def test_all_infeasible_returns_explicit_result(self) -> None:
        scenario = _set_statuses(get_scenario(DEMO), {"production-release": "done"})
        result = rank_options(scenario, _demo_payment_options())
        assert result["outcome"] == "no_feasible_recovery"
        assert result["recommendation"] is None
        assert result["ranked"] == []
        assert len(result["excluded"]) == 1

    def test_all_unscorable_returns_explicit_result(self) -> None:
        result = rank_options(
            get_scenario(DEMO), _demo_payment_options(), {"cost": 5.0}
        )
        assert result["outcome"] == "no_feasible_recovery"
        assert result["feasible_count"] == 1
        assert result["scorable_count"] == 0
        assert result["recommendation"] is None


class TestMalformedInputs:
    def test_options_must_be_a_non_empty_list(self) -> None:
        for bad in ("options", [], 42):
            with pytest.raises(ScoringError, match="non-empty list"):
                rank_options(get_scenario(DEMO), bad)  # type: ignore[arg-type]

    def test_option_must_be_a_dict(self) -> None:
        with pytest.raises(ScoringError, match=r"options\[0\] must be a dict"):
            rank_options(get_scenario(DEMO), ["nope"])

    def test_missing_option_id_raises(self) -> None:
        with pytest.raises(ScoringError, match="option_id"):
            rank_options(get_scenario(DEMO), [{"changes": []}])

    def test_blank_option_id_raises(self) -> None:
        with pytest.raises(ScoringError, match="option_id"):
            rank_options(get_scenario(DEMO), [{"option_id": "  "}])

    def test_non_string_option_id_raises(self) -> None:
        with pytest.raises(ScoringError, match="option_id"):
            rank_options(get_scenario(DEMO), [{"option_id": 42}])

    def test_duplicate_option_ids_raise(self) -> None:
        options = [
            {"option_id": "same", "changes": []},
            {"option_id": "same", "changes": []},
        ]
        with pytest.raises(ScoringError, match="duplicate option_id"):
            rank_options(get_scenario(DEMO), options)

    def test_malformed_option_id_raises(self) -> None:
        with pytest.raises(ScoringError, match="malformed option_id"):
            rank_options(get_scenario(DEMO), [{"option_id": "nonsense"}])

    def test_option_metrics_must_be_a_dict(self) -> None:
        options = [{"option_id": "a", "changes": [], "metrics": "cheap"}]
        with pytest.raises(ScoringError, match="'metrics' must be a dict"):
            rank_options(get_scenario(DEMO), options)

    def test_option_metrics_validated_even_before_scoring(self) -> None:
        options = [{"option_id": "a", "changes": [], "metrics": {"delay": "x"}}]
        with pytest.raises(ScoringError, match="must be a number"):
            rank_options(get_scenario(DEMO), options)

    def test_option_changes_must_be_a_list(self) -> None:
        options = [{"option_id": "a", "changes": "everything"}]
        with pytest.raises(ScoringError, match="'changes' must be a list"):
            rank_options(get_scenario(DEMO), options)

    def test_invalid_rank_weights_raise(self) -> None:
        with pytest.raises(ScoringError, match="unknown weight metric"):
            rank_options(get_scenario(DEMO), _demo_payment_options(), {"pace": 1.0})

    def test_malformed_scenario_raises_constraint_error(self) -> None:
        scenario = get_scenario(DEMO)
        del scenario["dependencies"]
        with pytest.raises(ConstraintError, match="missing scenario fields"):
            rank_options(scenario, _demo_payment_options())

    def test_malformed_custom_changes_propagate_constraint_error(self) -> None:
        options = [
            {
                "option_id": "bad-change",
                "changes": [
                    {
                        "action": "teleport",
                        "dependent": "payment-api",
                        "prerequisite": "backend-integration",
                    }
                ],
            }
        ]
        with pytest.raises(ConstraintError, match="unknown action"):
            rank_options(get_scenario(DEMO), options)


class TestReportShape:
    def test_report_key_order_is_stable(self) -> None:
        result = rank_options(get_scenario(DEMO), _demo_payment_options())
        assert list(result) == [
            "scenario_id",
            "plan_id",
            "plan_version",
            "simulated",
            "outcome",
            "weights",
            "feasible_count",
            "scorable_count",
            "recommendation",
            "ranked",
            "excluded",
            "assumptions",
            "limitations",
        ]
        assert result["scenario_id"] == DEMO
        assert result["plan_version"] == 1
        assert result["simulated"] is True
        assert result["weights"] == DEFAULT_WEIGHTS

    def test_entry_key_order_is_stable_for_ranked_and_excluded(self) -> None:
        scenario = _set_statuses(get_scenario(DEMO), {"production-release": "done"})
        result = rank_options(scenario, _demo_payment_options())
        expected = [
            "option_id",
            "rank",
            "feasible",
            "scorable",
            "score",
            "components",
            "metrics",
            "missing_metrics",
            "violations",
            "reasons",
        ]
        assert list(result["excluded"][0]) == expected

        ok = rank_options(get_scenario(DEMO), _demo_payment_options())
        assert list(ok["ranked"][0]) == expected

    def test_assumptions_and_limitations_state_policy(self) -> None:
        result = rank_options(get_scenario(DEMO), _demo_payment_options())
        joined = " ".join(result["assumptions"] + result["limitations"])
        assert "lower is better" in joined
        assert "tie-break" in joined
        assert "no cost or risk data" in joined
        assert "unitless" in joined


class TestDeterminism:
    def test_repeated_ranking_identical(self) -> None:
        first = rank_options(get_scenario(FULL), _full_integration_options())
        second = rank_options(get_scenario(FULL), _full_integration_options())
        assert first == second

    def test_repeated_ranking_identical_with_custom_weights(self) -> None:
        weights = {"cost": 2.0, "resource_strain": 1.0}
        first = rank_options(get_scenario(FULL), _full_integration_options(), weights)
        second = rank_options(get_scenario(FULL), _full_integration_options(), weights)
        assert first == second


class TestImmutability:
    def test_inputs_unchanged_after_success(self) -> None:
        scenario = get_scenario(FULL)
        options = _demo_payment_options()
        weights = {"resource_strain": 1.0}
        before_scenario = copy.deepcopy(scenario)
        before_options = copy.deepcopy(options)
        before_weights = copy.deepcopy(weights)
        rank_options(scenario, options, weights)
        assert scenario == before_scenario
        assert options == before_options
        assert weights == before_weights

    def test_inputs_unchanged_after_scoring_error(self) -> None:
        scenario = get_scenario(DEMO)
        options = [
            {"option_id": "same", "changes": []},
            {"option_id": "same", "changes": []},
        ]
        before_scenario = copy.deepcopy(scenario)
        before_options = copy.deepcopy(options)
        with pytest.raises(ScoringError):
            rank_options(scenario, options)
        assert scenario == before_scenario
        assert options == before_options

    def test_inputs_unchanged_after_constraint_error(self) -> None:
        scenario = get_scenario(DEMO)
        options = [
            {"option_id": "bad-change", "changes": [{"action": "teleport"}]}
        ]
        before_scenario = copy.deepcopy(scenario)
        before_options = copy.deepcopy(options)
        with pytest.raises(ConstraintError):
            rank_options(scenario, options)
        assert scenario == before_scenario
        assert options == before_options

    def test_mutating_result_does_not_corrupt_inputs(self) -> None:
        scenario = get_scenario(FULL)
        options = _demo_payment_options()
        before_options = copy.deepcopy(options)
        before_scenario = copy.deepcopy(scenario)
        result = rank_options(scenario, options)
        entry = result["ranked"][0]
        entry["metrics"].clear()
        entry["components"].clear()
        entry["missing_metrics"].clear()
        result["excluded"].clear()
        result["ranked"].clear()
        result["assumptions"].clear()
        assert options == before_options
        assert scenario == before_scenario
        fresh = get_scenario(DEMO)
        assert len(fresh["dependencies"]) == 4
