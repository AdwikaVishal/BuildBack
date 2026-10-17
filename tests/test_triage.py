"""Unit tests for blocker triage and the recovery view (Phase 6)."""

from __future__ import annotations

import copy

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.scenarios import SCENARIOS, get_scenario
from rippleback.server import analyze_blocked_tasks as triage_tool
from rippleback.triage import (
    STATE_AT_RISK,
    STATE_BLOCKED,
    STATE_DONE,
    STATE_EXTERNAL,
    STATE_HEALTHY,
    TriageError,
    analyze_blocked_tasks,
    recompute_with_changes,
)


def _scenario(scenario_id: str = "project-full") -> dict:
    return get_scenario(scenario_id)


def _states(result: dict) -> dict[str, str]:
    return {entry["entity_id"]: entry["state"] for entry in result["states"]}


class TestProjectOutageTriage:
    def test_exactly_one_state_per_entity(self) -> None:
        scenario = _scenario()
        result = analyze_blocked_tasks(scenario, entity_id="payment-api")
        assert len(result["states"]) == len(scenario["entities"])
        assert len({entry["entity_id"] for entry in result["states"]}) == len(
            scenario["entities"]
        )
        assert set(_states(result).values()) <= {
            STATE_DONE,
            STATE_BLOCKED,
            STATE_EXTERNAL,
            STATE_AT_RISK,
            STATE_HEALTHY,
        }

    def test_payment_chain_is_blocked(self) -> None:
        result = analyze_blocked_tasks(_scenario(), entity_id="payment-api")
        states = _states(result)
        for entity_id in (
            "payment-api",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ):
            assert states[entity_id] == STATE_BLOCKED, entity_id

    def test_finished_work_is_done(self) -> None:
        states = _states(analyze_blocked_tasks(_scenario(), entity_id="payment-api"))
        assert states["auth-service"] == STATE_DONE

    def test_independent_work_is_healthy(self) -> None:
        states = _states(analyze_blocked_tasks(_scenario(), entity_id="payment-api"))
        assert states["frontend-integration"] == STATE_HEALTHY
        assert states["vendor-delivery"] == STATE_HEALTHY

    def test_soft_edge_is_at_risk(self) -> None:
        states = _states(analyze_blocked_tasks(_scenario(), entity_id="payment-api"))
        assert states["unit-testing"] == STATE_AT_RISK

    def test_low_slack_is_at_risk(self) -> None:
        states = _states(analyze_blocked_tasks(_scenario(), entity_id="payment-api"))
        assert states["release-notes"] == STATE_AT_RISK

    def test_parallel_work_lists_only_work_that_can_proceed(self) -> None:
        result = analyze_blocked_tasks(_scenario(), entity_id="payment-api")
        parallel = {entry["entity_id"] for entry in result["parallel_work"]}
        assert parallel == {
            "frontend-integration",
            "vendor-delivery",
            "unit-testing",
            "release-notes",
        }
        assert parallel.isdisjoint(
            {"payment-api", "backend-integration", "auth-service"}
        )

    def test_evidence_paths_are_traceable(self) -> None:
        scenario = _scenario()
        lookup = {
            (dep["prerequisite"], dep["dependent"]) for dep in scenario["dependencies"]
        }
        result = analyze_blocked_tasks(scenario, entity_id="payment-api")
        for entry in result["states"]:
            path = entry["evidence_path"]
            if entry["state"] in {STATE_BLOCKED, STATE_EXTERNAL}:
                assert path[0] == "payment-api"
                assert path[-1] == entry["entity_id"]
                for source, target in zip(path, path[1:]):
                    assert (source, target) in lookup

    def test_summary_counts_match_states(self) -> None:
        result = analyze_blocked_tasks(_scenario(), entity_id="payment-api")
        counted: dict[str, int] = {}
        for entry in result["states"]:
            counted[entry["state"]] = counted.get(entry["state"], 0) + 1
        assert {k: v for k, v in result["summary"].items() if v} == counted


class TestExternalTriage:
    def test_delayed_external_vendor_makes_dependents_external(self) -> None:
        result = analyze_blocked_tasks(
            _scenario(),
            entity_id="vendor-delivery",
            event_type="delivery_delay",
            parameters={"delay_minutes": 480},
        )
        states = _states(result)
        assert states["vendor-delivery"] == STATE_EXTERNAL
        assert states["backend-integration"] == STATE_EXTERNAL
        assert states["production-release"] == STATE_EXTERNAL
        assert states["payment-api"] == STATE_HEALTHY


class TestTravelDelayTriage:
    def test_not_every_downstream_event_fails(self) -> None:
        result = analyze_blocked_tasks(
            _scenario("travel"),
            entity_id="leg-1",
            event_type="transport_delay",
            parameters={"delay_minutes": 120},
        )
        states = _states(result)
        assert states["leg-1"] == STATE_BLOCKED
        assert states["transfer"] == STATE_BLOCKED
        assert states["connecting-leg"] == STATE_BLOCKED
        assert states["hotel-check-in"] == STATE_AT_RISK
        assert states["meeting"] == STATE_HEALTHY

    def test_larger_delay_blocks_more(self) -> None:
        result = analyze_blocked_tasks(
            _scenario("travel"),
            entity_id="leg-1",
            event_type="transport_delay",
            parameters={"delay_minutes": 300},
        )
        states = _states(result)
        assert states["hotel-check-in"] == STATE_BLOCKED
        assert states["meeting"] == STATE_BLOCKED


class TestRecomputeWithChanges:
    def test_removing_broken_edge_unblocks_chain(self) -> None:
        scenario = _scenario()
        result = recompute_with_changes(
            scenario,
            entity_id="payment-api",
            changes=[
                {
                    "action": "remove_dependency",
                    "dependent": "backend-integration",
                    "prerequisite": "payment-api",
                }
            ],
        )
        changes = {c["entity_id"]: c for c in result["state_changes"]}
        assert changes["backend-integration"]["before"] == STATE_BLOCKED
        assert changes["backend-integration"]["after"] == STATE_HEALTHY
        assert changes["production-release"]["after"] == STATE_HEALTHY

    def test_recompute_does_not_mutate_committed_plan(self) -> None:
        before = copy.deepcopy(SCENARIOS["project-full"])
        recompute_with_changes(
            _scenario(),
            entity_id="payment-api",
            changes=[
                {
                    "action": "remove_dependency",
                    "dependent": "backend-integration",
                    "prerequisite": "payment-api",
                }
            ],
        )
        assert SCENARIOS["project-full"] == before

    def test_unknown_action_rejected(self) -> None:
        with pytest.raises(TriageError, match="unknown action"):
            recompute_with_changes(
                _scenario(),
                entity_id="payment-api",
                changes=[
                    {
                        "action": "detonate",
                        "dependent": "backend-integration",
                        "prerequisite": "payment-api",
                    }
                ],
            )


class TestTriageErrors:
    def test_unknown_entity(self) -> None:
        with pytest.raises(TriageError, match="unknown entity_id"):
            analyze_blocked_tasks(_scenario(), entity_id="ghost")

    def test_unknown_event_type(self) -> None:
        with pytest.raises(TriageError, match="unknown event_type"):
            analyze_blocked_tasks(
                _scenario(), entity_id="payment-api", event_type="meteor"
            )

    def test_delay_event_requires_delay_minutes(self) -> None:
        with pytest.raises(TriageError, match="delay_minutes"):
            analyze_blocked_tasks(
                _scenario(), entity_id="payment-api", event_type="transport_delay"
            )

    def test_tool_wraps_unknown_scenario(self) -> None:
        with pytest.raises(ToolError, match="unknown scenario_id"):
            triage_tool("no-such-scenario", "payment-api")

    def test_tool_wraps_unknown_entity(self) -> None:
        with pytest.raises(ToolError, match="unknown entity_id"):
            triage_tool("project-full", "ghost")


class TestDeterminism:
    def test_repeated_calls_identical(self) -> None:
        first = analyze_blocked_tasks(_scenario(), entity_id="payment-api")
        second = analyze_blocked_tasks(_scenario(), entity_id="payment-api")
        assert first == second

    def test_simulated_flag_set(self) -> None:
        result = analyze_blocked_tasks(_scenario(), entity_id="payment-api")
        assert result["simulated"] is True
        assert result["plan_version"] == 1
