"""Unit tests for the deterministic recovery-plan evaluation engine (Phase 4)."""

from __future__ import annotations

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.evaluation import (
    EvaluationError,
    evaluate_recovery_plan,
    parse_option_id,
)
from rippleback.scenarios import SCENARIOS, get_scenario
from rippleback.server import evaluate_recovery_plan as evaluate_tool

SCENARIO_ID = "demo-release-plan"
OPTION_ID = "drop-dependency:backend-integration->payment-api"


def _scenario() -> dict:
    return get_scenario(SCENARIO_ID)


def _branching_scenario() -> dict:
    """alpha and beta both require core; release-validation requires both."""
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


class TestParseOptionId:
    def test_splits_dependent_and_prerequisite(self) -> None:
        assert parse_option_id(OPTION_ID) == ("backend-integration", "payment-api")

    @pytest.mark.parametrize(
        "bad",
        ["", "nope", "drop-dependency:", "drop-dependency:only", "drop-dependency:a->", "drop-dependency:->b", "withdraw:a->b"],
    )
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(EvaluationError, match="malformed option_id"):
            parse_option_id(bad)


class TestValidProposal:
    def test_verdict_is_structural(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        assert report["verdict"] == "structurally_valid"

    def test_report_shape(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        for field in (
            "evaluation_id",
            "scenario_id",
            "plan_id",
            "plan_version",
            "expected_plan_version",
            "verdict",
            "dependency",
            "findings",
            "impact",
            "assumptions",
            "limitations",
        ):
            assert field in report, f"missing field: {field}"
        assert report["scenario_id"] == SCENARIO_ID
        assert report["plan_id"] == "plan-payment-api-release"
        assert report["plan_version"] == 1
        assert report["expected_plan_version"] == 1
        assert report["dependency"] == {
            "dependent": "backend-integration",
            "prerequisite": "payment-api",
        }
        assert report["assumptions"] and report["limitations"]

    def test_evaluation_id_is_deterministic_and_informative(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        assert report["evaluation_id"] == (
            "eval:demo-release-plan:"
            "drop-dependency:backend-integration->payment-api:v1"
        )

    def test_business_readiness_is_not_claimed(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        codes = {finding["code"] for finding in report["findings"]}
        assert "business_readiness_not_assessed" in codes
        warning = next(
            f
            for f in report["findings"]
            if f["code"] == "business_readiness_not_assessed"
        )
        assert warning["level"] == "warning"

    def test_impact_uses_residual_graph(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        impact = report["impact"]
        assert impact["disrupted_entity_id"] == "payment-api"
        assert impact["removed_dependency"] == {
            "dependent": "backend-integration",
            "prerequisite": "payment-api",
        }
        assert impact["unblocks"] == [
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert impact["still_blocked"] == []

    def test_findings_have_code_level_message(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        for finding in report["findings"]:
            assert set(finding) == {"code", "level", "message"}
            assert finding["level"] in {"info", "warning", "error"}
            assert finding["message"]


class TestRejections:
    def _verdicts(self, report: dict) -> set[str]:
        return {f["code"] for f in report["findings"]}

    def test_missing_edge_is_rejected(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(),
            option_id="drop-dependency:payment-api->backend-integration",
            expected_plan_version=1,
        )
        assert report["verdict"] == "rejected"
        assert "missing_dependency" in self._verdicts(report)
        assert report["impact"] is None

    def test_stale_plan_version_is_rejected(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=99
        )
        assert report["verdict"] == "rejected"
        assert "stale_plan_version" in self._verdicts(report)
        assert report["plan_version"] == 1
        assert report["expected_plan_version"] == 99
        assert report["impact"] is None

    def test_unknown_dependent_is_rejected(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id="drop-dependency:ghost->payment-api", expected_plan_version=1
        )
        assert report["verdict"] == "rejected"
        assert "unknown_entity" in self._verdicts(report)
        assert report["impact"] is None

    def test_unknown_prerequisite_is_rejected(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id="drop-dependency:backend-integration->ghost", expected_plan_version=1
        )
        assert report["verdict"] == "rejected"
        assert "unknown_entity" in self._verdicts(report)
        assert report["impact"] is None

    def test_rejected_reports_still_carry_assumptions_and_limitations(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=99
        )
        assert report["assumptions"] and report["limitations"]


class TestBranchingPrerequisites:
    def test_removing_one_edge_keeps_other_prerequisite_blocked(self) -> None:
        report = evaluate_recovery_plan(
            _branching_scenario(),
            option_id="drop-dependency:alpha->core",
            expected_plan_version=3,
        )
        assert report["verdict"] == "structurally_valid"
        impact = report["impact"]
        assert impact["unblocks"] == ["alpha"]
        assert impact["still_blocked"] == ["beta", "release-validation"]

    def test_removing_other_edge_symmetric(self) -> None:
        report = evaluate_recovery_plan(
            _branching_scenario(),
            option_id="drop-dependency:beta->core",
            expected_plan_version=3,
        )
        impact = report["impact"]
        assert impact["unblocks"] == ["beta"]
        assert impact["still_blocked"] == ["alpha", "release-validation"]

    def test_dependent_with_remaining_path_is_still_blocked(self) -> None:
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
        report = evaluate_recovery_plan(
            scenario, option_id="drop-dependency:alpha->core", expected_plan_version=1
        )
        impact = report["impact"]
        assert "alpha" not in impact["unblocks"]
        assert impact["unblocks"] == []
        assert impact["still_blocked"] == ["relay", "alpha"]


class TestInvalidInput:
    def test_malformed_option_id_raises(self) -> None:
        with pytest.raises(EvaluationError, match="malformed option_id"):
            evaluate_recovery_plan(
                _scenario(), option_id="not-an-option", expected_plan_version=1
            )

    def test_non_integer_version_raises(self) -> None:
        with pytest.raises(EvaluationError, match="expected_plan_version"):
            evaluate_recovery_plan(
                _scenario(), option_id=OPTION_ID, expected_plan_version="1"  # type: ignore[arg-type]
            )

    def test_bool_version_raises(self) -> None:
        with pytest.raises(EvaluationError, match="expected_plan_version"):
            evaluate_recovery_plan(
                _scenario(), option_id=OPTION_ID, expected_plan_version=True  # type: ignore[arg-type]
            )

    def test_unknown_scenario_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="unknown scenario_id"):
            evaluate_tool("no-such-scenario", OPTION_ID, 1)

    def test_malformed_option_tool_raises(self) -> None:
        with pytest.raises(ToolError, match="malformed option_id"):
            evaluate_tool(SCENARIO_ID, "not-an-option", 1)


class TestDeterminism:
    def test_repeated_calls_identical(self) -> None:
        first = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        second = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        assert first == second

    def test_repeated_branching_calls_identical(self) -> None:
        first = evaluate_recovery_plan(
            _branching_scenario(), option_id="drop-dependency:alpha->core", expected_plan_version=3
        )
        second = evaluate_recovery_plan(
            _branching_scenario(), option_id="drop-dependency:alpha->core", expected_plan_version=3
        )
        assert first == second

    def test_findings_order_is_stable(self) -> None:
        report = evaluate_recovery_plan(
            _scenario(), option_id=OPTION_ID, expected_plan_version=1
        )
        assert [f["code"] for f in report["findings"]] == [
            "plan_version_match",
            "dependency_edge_exists",
            "business_readiness_not_assessed",
        ]


class TestNonMutation:
    def test_committed_fixture_unchanged(self) -> None:
        before = copy.deepcopy(SCENARIOS[SCENARIO_ID])
        evaluate_tool(SCENARIO_ID, OPTION_ID, 1)
        assert SCENARIOS[SCENARIO_ID] == before

    def test_scenario_object_not_mutated(self) -> None:
        scenario = _scenario()
        before = copy.deepcopy(scenario)
        evaluate_recovery_plan(
            scenario, option_id=OPTION_ID, expected_plan_version=1
        )
        assert scenario == before

    def test_mutating_result_does_not_corrupt_input(self) -> None:
        scenario = _branching_scenario()
        before = copy.deepcopy(scenario)
        report = evaluate_recovery_plan(
            scenario, option_id="drop-dependency:alpha->core", expected_plan_version=3
        )
        report["findings"].append({"code": "injected", "level": "info", "message": "x"})
        report["impact"]["unblocks"].append("injected")
        assert scenario == before
