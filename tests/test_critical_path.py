"""Unit tests for the longest-path critical path and disruption changes (Phase 6)."""

from __future__ import annotations

import copy

from rippleback.critical_path import critical_path, critical_path_changes
from rippleback.scenarios import SCENARIOS, get_scenario


class TestCriticalPath:
    def test_demo_plan_uses_steps_when_no_durations(self) -> None:
        result = critical_path(get_scenario("demo-release-plan"))
        assert result["unit"] == "steps"
        assert result["path"] == [
            "payment-api",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert result["length"] == 5.0

    def test_project_full_weighted_by_duration(self) -> None:
        result = critical_path(get_scenario("project-full"))
        assert result["unit"] == "duration_units"
        assert result["path"] == [
            "payment-api",
            "backend-integration",
            "integration-testing",
            "release-validation",
            "production-release",
        ]
        assert result["length"] == 9.0

    def test_travel_weighted_by_duration(self) -> None:
        result = critical_path(get_scenario("travel"))
        assert result["path"] == [
            "leg-1",
            "transfer",
            "connecting-leg",
            "hotel-check-in",
            "meeting",
        ]
        assert result["length"] == 300.0

    def test_independent_work_not_on_path(self) -> None:
        result = critical_path(get_scenario("project-full"))
        assert "frontend-integration" not in result["path"]
        assert "auth-service" not in result["path"]

    def test_soft_edge_never_defines_path(self) -> None:
        # unit-testing benefits_from integration-testing; it must not extend the path.
        result = critical_path(get_scenario("project-full"))
        assert "unit-testing" not in result["path"]

    def test_edges_join_consecutive_nodes(self) -> None:
        result = critical_path(get_scenario("project-full"))
        joined = [
            (edge["prerequisite"], edge["dependent"]) for edge in result["edges"]
        ]
        assert joined == list(zip(result["path"], result["path"][1:]))

    def test_deterministic(self) -> None:
        first = critical_path(get_scenario("project-full"))
        second = critical_path(get_scenario("project-full"))
        assert first == second

    def test_does_not_mutate_fixture(self) -> None:
        before = copy.deepcopy(SCENARIOS["travel"])
        critical_path(get_scenario("travel"))
        assert SCENARIOS["travel"] == before


class TestCriticalPathChanges:
    def test_payment_outage_intersects_baseline(self) -> None:
        result = critical_path_changes(get_scenario("project-full"), "payment-api")
        assert result["baseline_intersects_disruption"] is True
        assert result["disrupted_path"][0] == "payment-api"
        assert "production-release" in result["disrupted_path"]

    def test_independent_disruption_has_short_path(self) -> None:
        result = critical_path_changes(get_scenario("project-full"), "frontend-integration")
        assert result["disrupted_path"] == ["frontend-integration"]
        assert result["baseline_intersects_disruption"] is False

    def test_external_root_flagged(self) -> None:
        result = critical_path_changes(get_scenario("project-full"), "vendor-delivery")
        assert result["disrupted_entity_is_external"] is True
