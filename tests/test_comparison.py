"""Unit tests for recovery-option comparison with forecasts (Person 3, Milestone 4)."""

from __future__ import annotations

import copy

import pytest

from rippleback.comparison import compare_recovery_options
from rippleback.constraints import ConstraintError
from rippleback.recovery import find_alternative_dependencies
from rippleback.scenarios import get_scenario
from rippleback.scoring import DEFAULT_WEIGHTS, ScoringError, rank_options

DEMO = "demo-release-plan"
FULL = "project-full"

FULL_RELEASE_REMOVAL = "drop-dependency:release-validation->integration-testing"
FULL_SOFT_REMOVAL = "drop-dependency:unit-testing->integration-testing"
DEMO_PAYMENT_REMOVAL = "drop-dependency:backend-integration->payment-api"
REVERSED_ID = "drop-dependency:payment-api->backend-integration"

CYCLE_CHANGES = [
    {
        "action": "add_dependency",
        "dependent": "payment-api",
        "prerequisite": "production-release",
    }
]
WIDEN_CHANGES = [
    {
        "action": "add_dependency",
        "dependent": "frontend-integration",
        "prerequisite": "production-release",
    }
]

REPORT_KEYS = [
    "scenario_id",
    "plan_id",
    "plan_version",
    "simulated",
    "outcome",
    "weights",
    "feasible_count",
    "scorable_count",
    "recommendation",
    "baseline",
    "ranked",
    "excluded",
    "assumptions",
    "limitations",
]
ENTRY_KEYS = [
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
    "forecast",
    "no_op",
    "assumptions",
    "limitations",
]
FORECAST_KEYS = [
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
]
BASELINE_KEYS = ["path", "length", "unit", "edges", "assumptions", "limitations"]

FULL_BASELINE_PATH = [
    "payment-api",
    "backend-integration",
    "integration-testing",
    "release-validation",
    "production-release",
]


def _full_options() -> list[dict]:
    return find_alternative_dependencies(
        get_scenario(FULL), entity_id="integration-testing"
    )["options"]


def _demo_options() -> list[dict]:
    return find_alternative_dependencies(
        get_scenario(DEMO), entity_id="payment-api"
    )["options"]


def _entry_by_id(report: dict, option_id: str) -> dict:
    matches = [
        entry
        for entry in (*report["ranked"], *report["excluded"])
        if entry["option_id"] == option_id
    ]
    assert len(matches) == 1
    return matches[0]


def _gate_violated_scenario() -> dict:
    scenario = get_scenario(DEMO)
    for entity in scenario["entities"]:
        if entity["id"] == "production-release":
            entity["status"] = "done"
    return scenario


class TestReportShape:
    def test_report_key_order_and_echoes(self) -> None:
        scenario = get_scenario(FULL)
        report = compare_recovery_options(scenario, _full_options())
        assert list(report) == REPORT_KEYS
        assert report["scenario_id"] == FULL
        assert report["plan_id"] == scenario["plan_id"]
        assert report["plan_version"] == scenario["version"]
        assert report["simulated"] is True
        assert report["outcome"] == "ranked"
        assert report["weights"] == DEFAULT_WEIGHTS
        assert report["feasible_count"] == 2
        assert report["scorable_count"] == 2
        assert report["recommendation"] == FULL_RELEASE_REMOVAL

    def test_baseline_is_full_critical_path_report(self) -> None:
        report = compare_recovery_options(get_scenario(FULL), _full_options())
        baseline = report["baseline"]
        assert list(baseline) == BASELINE_KEYS
        assert baseline["length"] == 9.0
        assert baseline["unit"] == "duration_units"
        assert baseline["path"] == FULL_BASELINE_PATH
        assert len(baseline["edges"]) == 4
        for entry in (*report["ranked"], *report["excluded"]):
            assert entry["forecast"]["baseline_length"] == baseline["length"]

    def test_entry_key_order_for_ranked_and_excluded(self) -> None:
        options = [
            *_full_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
            {"option_id": REVERSED_ID},
        ]
        report = compare_recovery_options(get_scenario(FULL), options)
        assert report["ranked"] and report["excluded"]
        for entry in report["ranked"]:
            assert list(entry) == ENTRY_KEYS
        for entry in report["excluded"]:
            assert list(entry) == ENTRY_KEYS

    def test_simulation_flags_everywhere(self) -> None:
        options = [
            *_full_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
        ]
        report = compare_recovery_options(get_scenario(FULL), options)
        assert report["simulated"] is True
        for entry in (*report["ranked"], *report["excluded"]):
            forecast = entry["forecast"]
            assert forecast["simulated"] is True
            assert forecast["scenario_id"] == FULL
            assert forecast["plan_version"] == 1


class TestBaselineAndForecastAttachment:
    def test_total_baseline_is_distinct_from_delay_delta(self) -> None:
        report = compare_recovery_options(get_scenario(FULL), _full_options())
        entry = _entry_by_id(report, FULL_RELEASE_REMOVAL)
        forecast = entry["forecast"]
        assert report["baseline"]["length"] == 9.0
        assert forecast["baseline_length"] == 9.0
        assert forecast["forecast_length"] == 7.0
        assert forecast["delay_delta"] == -2.0
        assert forecast["delay_delta"] == forecast["forecast_length"] - forecast["baseline_length"]
        assert entry["score"] == -2.0

    def test_forecast_has_no_disruption_context(self) -> None:
        report = compare_recovery_options(get_scenario(FULL), _full_options())
        entry = _entry_by_id(report, FULL_RELEASE_REMOVAL)
        forecast = entry["forecast"]
        assert list(forecast) == FORECAST_KEYS
        assert forecast["disrupted_entity_id"] is None
        assert forecast["option_id"] is None
        assert forecast["changes"] == [
            {
                "action": "remove_dependency",
                "dependent": "release-validation",
                "prerequisite": "integration-testing",
            }
        ]
        assert forecast["feasible"] is True
        assert forecast["error"] is None

    def test_default_weights_score_is_derived_delay(self) -> None:
        report = compare_recovery_options(get_scenario(FULL), _full_options())
        for entry in report["ranked"]:
            assert entry["score"] == entry["metrics"]["delay"]
            assert entry["metrics"]["delay"] == entry["forecast"]["delay_delta"]
        soft = _entry_by_id(report, FULL_SOFT_REMOVAL)
        assert soft["metrics"]["resource_strain"] == 8.0

    def test_caller_delay_override_changes_score_not_forecast(self) -> None:
        options = [dict(_demo_options()[0], metrics={"delay": 100.0})]
        report = compare_recovery_options(get_scenario(DEMO), options)
        entry = report["ranked"][0]
        assert entry["metrics"]["delay"] == 100.0
        assert entry["score"] == 100.0
        assert entry["forecast"]["delay_delta"] == -1.0


class TestRankingIntegration:
    def test_matches_rank_options_report(self) -> None:
        scenario = get_scenario(FULL)
        options = _full_options()
        ranking = rank_options(scenario, options)
        report = compare_recovery_options(scenario, options)
        for key in (
            "outcome",
            "weights",
            "feasible_count",
            "scorable_count",
            "recommendation",
        ):
            assert report[key] == ranking[key]
        assert report["assumptions"][: len(ranking["assumptions"])] == ranking[
            "assumptions"
        ]
        assert report["limitations"][: len(ranking["limitations"])] == ranking[
            "limitations"
        ]
        for mine, theirs in zip(report["ranked"], ranking["ranked"]):
            for key, value in theirs.items():
                assert mine[key] == value
        for mine, theirs in zip(report["excluded"], ranking["excluded"]):
            for key, value in theirs.items():
                assert mine[key] == value

    def test_recommendation_is_rank_one_and_scores_ordered(self) -> None:
        report = compare_recovery_options(get_scenario(FULL), _full_options())
        ranked = report["ranked"]
        assert [entry["rank"] for entry in ranked] == list(
            range(1, len(ranked) + 1)
        )
        assert report["recommendation"] == ranked[0]["option_id"]
        scores = [entry["score"] for entry in ranked]
        assert scores == sorted(scores)

    def test_equal_scores_tie_break_by_option_id(self) -> None:
        options = [
            {"option_id": "b-noop", "changes": []},
            {"option_id": "a-noop", "changes": []},
        ]
        report = compare_recovery_options(get_scenario(DEMO), options)
        assert [entry["option_id"] for entry in report["ranked"]] == [
            "a-noop",
            "b-noop",
        ]
        assert [entry["rank"] for entry in report["ranked"]] == [1, 2]
        assert report["recommendation"] == "a-noop"

    def test_weights_change_the_ranking(self) -> None:
        options = [
            {**option, "metrics": {"cost": cost}}
            for option, cost in zip(_full_options(), [50.0, 1.0])
        ]
        report = compare_recovery_options(
            get_scenario(FULL), options, weights={"delay": 0.0, "cost": 1.0}
        )
        assert report["weights"]["cost"] == 1.0
        assert report["recommendation"] == FULL_SOFT_REMOVAL
        assert [entry["option_id"] for entry in report["ranked"]] == [
            FULL_SOFT_REMOVAL,
            FULL_RELEASE_REMOVAL,
        ]
        assert [entry["score"] for entry in report["ranked"]] == [1.0, 50.0]


class TestNoOpSemantics:
    def test_edge_miss_option_excluded_as_noop(self) -> None:
        report = compare_recovery_options(get_scenario(DEMO), [{"option_id": REVERSED_ID}])
        assert report["outcome"] == "no_feasible_recovery"
        assert report["feasible_count"] == 0
        entry = report["excluded"][0]
        assert entry["no_op"] is True
        assert entry["score"] is None
        assert any("no-op recovery" in reason for reason in entry["reasons"])
        assert entry["forecast"]["changes"] == []
        assert entry["forecast"]["delay_delta"] == 0.0
        assert any("(no-op)" in limit for limit in entry["limitations"])

    def test_explicit_empty_changes_ranked_with_noop_flag(self) -> None:
        report = compare_recovery_options(
            get_scenario(DEMO), [{"option_id": "keep-baseline", "changes": []}]
        )
        entry = report["ranked"][0]
        assert entry["rank"] == 1
        assert entry["score"] == 0.0
        assert entry["no_op"] is True
        assert entry["forecast"]["delay_delta"] == 0.0
        assert any("(no-op)" in limit for limit in entry["limitations"])

    def test_soft_edge_removal_is_not_a_noop(self) -> None:
        report = compare_recovery_options(get_scenario(FULL), _full_options())
        entry = _entry_by_id(report, FULL_SOFT_REMOVAL)
        assert entry["no_op"] is False
        assert entry["forecast"]["delay_delta"] == 0.0
        assert not any("(no-op)" in limit for limit in entry["limitations"])

    def test_noop_recommendation_limited_to_keep_baseline(self) -> None:
        options = [
            {"option_id": "keep-baseline", "changes": []},
            {"option_id": "widen", "changes": WIDEN_CHANGES},
        ]
        report = compare_recovery_options(get_scenario(DEMO), options)
        assert report["recommendation"] == "keep-baseline"
        assert _entry_by_id(report, "keep-baseline")["no_op"] is True
        assert _entry_by_id(report, "widen")["no_op"] is False
        assert _entry_by_id(report, "widen")["score"] == 1.0
        assert any(
            "keep the baseline" in limit for limit in report["limitations"]
        )

    def test_real_improvement_recommendation_has_no_keep_baseline_note(self) -> None:
        report = compare_recovery_options(get_scenario(DEMO), _demo_options())
        entry = _entry_by_id(report, DEMO_PAYMENT_REMOVAL)
        assert report["recommendation"] == DEMO_PAYMENT_REMOVAL
        assert entry["no_op"] is False
        assert entry["score"] == -1.0
        assert not any(
            "keep the baseline" in limit for limit in report["limitations"]
        )


class TestExclusionsNeverRecommended:
    def test_cycle_option_excluded_with_infeasible_forecast(self) -> None:
        options = [
            *_demo_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
        ]
        report = compare_recovery_options(get_scenario(DEMO), options)
        assert report["recommendation"] == DEMO_PAYMENT_REMOVAL
        assert "make-cycle" not in [
            entry["option_id"] for entry in report["ranked"]
        ]
        entry = _entry_by_id(report, "make-cycle")
        assert entry["feasible"] is False
        assert entry["score"] is None
        assert [violation["code"] for violation in entry["violations"]] == [
            "CYCLIC_DEPENDENCY"
        ]
        assert entry["no_op"] is False
        assert entry["forecast"]["feasible"] is False
        assert entry["forecast"]["error"]["code"] == "CYCLIC_DEPENDENCY"
        assert entry["forecast"]["forecast_length"] is None
        assert entry["forecast"]["delay_delta"] is None

    def test_constraint_violation_excludes_every_option(self) -> None:
        report = compare_recovery_options(_gate_violated_scenario(), _demo_options())
        assert report["outcome"] == "no_feasible_recovery"
        assert report["feasible_count"] == 0
        assert report["scorable_count"] == 0
        assert report["recommendation"] is None
        entry = report["excluded"][0]
        assert entry["feasible"] is False
        assert [violation["code"] for violation in entry["violations"]] == [
            "CONSTRAINT_VIOLATED"
        ]
        assert entry["forecast"]["feasible"] is True
        assert entry["forecast"]["delay_delta"] == -1.0
        assert any(
            "No option was both feasible and scorable" in limit
            for limit in report["limitations"]
        )

    def test_unscorable_option_never_recommended(self) -> None:
        report = compare_recovery_options(
            get_scenario(DEMO), _demo_options(), weights={"resource_strain": 1.0}
        )
        assert report["outcome"] == "no_feasible_recovery"
        assert report["feasible_count"] == 1
        assert report["scorable_count"] == 0
        assert report["recommendation"] is None
        entry = report["excluded"][0]
        assert entry["feasible"] is True
        assert entry["scorable"] is False
        assert entry["score"] is None
        assert any("resource_strain" in reason for reason in entry["reasons"])
        assert entry["forecast"]["feasible"] is True
        assert entry["forecast"]["delay_delta"] == -1.0

    def test_excluded_entries_never_appear_in_recommendation(self) -> None:
        options = [
            *_full_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
            {"option_id": REVERSED_ID},
        ]
        report = compare_recovery_options(get_scenario(FULL), options)
        excluded_ids = [entry["option_id"] for entry in report["excluded"]]
        ranked_ids = [entry["option_id"] for entry in report["ranked"]]
        assert len(excluded_ids) == 2
        assert report["recommendation"] in ranked_ids
        assert report["recommendation"] not in excluded_ids
        assert all(
            entry["rank"] is None and entry["score"] is None
            for entry in report["excluded"]
        )


class TestMalformedInputs:
    def test_options_must_be_a_non_empty_list(self) -> None:
        with pytest.raises(ScoringError, match="non-empty list"):
            compare_recovery_options(get_scenario(DEMO), [])

    def test_duplicate_option_ids_raise(self) -> None:
        options = [
            {"option_id": "twin", "changes": []},
            {"option_id": "twin", "changes": []},
        ]
        with pytest.raises(ScoringError, match="duplicate option_id"):
            compare_recovery_options(get_scenario(DEMO), options)

    def test_unknown_weight_key_raises(self) -> None:
        with pytest.raises(ScoringError, match="unknown weight"):
            compare_recovery_options(
                get_scenario(DEMO), _demo_options(), weights={"effort": 1.0}
            )

    def test_malformed_scenario_raises_constraint_error(self) -> None:
        scenario = get_scenario(DEMO)
        scenario.pop("version")
        with pytest.raises(ConstraintError, match="malformed scenario"):
            compare_recovery_options(scenario, _demo_options())

    def test_malformed_custom_changes_propagate_constraint_error(self) -> None:
        options = [
            {
                "option_id": "boom",
                "changes": [
                    {
                        "action": "explode",
                        "dependent": "payment-api",
                        "prerequisite": "backend-integration",
                    }
                ],
            }
        ]
        with pytest.raises(ConstraintError, match="unknown action"):
            compare_recovery_options(get_scenario(DEMO), options)


class TestDeterminismAndImmutability:
    def test_repeated_calls_identical(self) -> None:
        scenario = get_scenario(FULL)
        options = [
            *_full_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
        ]
        first = compare_recovery_options(scenario, options)
        second = compare_recovery_options(scenario, options)
        assert first == second

    def test_inputs_unchanged_after_call(self) -> None:
        scenario = get_scenario(FULL)
        options = [
            *_full_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
            {"option_id": REVERSED_ID},
        ]
        weights = {"delay": 0.5}
        pristine_scenario = copy.deepcopy(scenario)
        pristine_options = copy.deepcopy(options)
        pristine_weights = copy.deepcopy(weights)
        compare_recovery_options(scenario, options, weights)
        assert scenario == pristine_scenario
        assert options == pristine_options
        assert weights == pristine_weights

    def test_mutating_result_does_not_corrupt_inputs(self) -> None:
        scenario = get_scenario(FULL)
        options = [
            *_full_options(),
            {"option_id": "make-cycle", "changes": CYCLE_CHANGES},
        ]
        pristine_scenario = copy.deepcopy(scenario)
        pristine_options = copy.deepcopy(options)
        report = compare_recovery_options(scenario, options)
        top = report["ranked"][0]
        top["forecast"]["forecast_path"].clear()
        top["forecast"]["baseline_path"].clear()
        top["forecast"]["changes"].clear()
        top["metrics"].clear()
        top["assumptions"].append("tampered")
        report["baseline"]["path"].clear()
        report["baseline"]["edges"].clear()
        assert scenario == pristine_scenario
        assert options == pristine_options

    def test_input_mutation_after_call_does_not_change_report(self) -> None:
        scenario = get_scenario(DEMO)
        options = _demo_options()
        report = compare_recovery_options(scenario, options)
        snapshot = copy.deepcopy(report)
        scenario["dependencies"].append(
            {"dependent": "release-validation", "prerequisite": "payment-api"}
        )
        scenario["entities"][0]["name"] = "renamed"
        options[0]["metrics"] = {"cost": 7.0}
        options[0]["assumptions"].append("late assumption")
        options.append({"option_id": "late-option", "changes": []})
        assert report == snapshot
