import copy
import random
from typing import Any

import pytest

from rippleback.constraints import ConstraintError
from rippleback.scheduler import SchedulerError, generate_recovery_schedule
from rippleback.scenarios import get_scenario

DEMO = "demo-release-plan"
PROJECT = "project-full"
TRAVEL = "travel"

CYCLE_CHANGE = {
    "action": "add_dependency",
    "dependent": "payment-api",
    "prerequisite": "production-release",
}
INVALID_REF_CHANGE = {
    "action": "remove_dependency",
    "dependent": "nope",
    "prerequisite": "production-release",
}
HARD_CONSTRAINT_VIOLATION = {
    "action": "add_dependency",
    "dependent": "integration-testing",
    "prerequisite": "release-validation",
}


@pytest.fixture()
def project_full() -> dict[str, Any]:
    return copy.deepcopy(get_scenario(PROJECT))


@pytest.fixture()
def demo_plan() -> dict[str, Any]:
    return copy.deepcopy(get_scenario(DEMO))


@pytest.fixture()
def travel_plan() -> dict[str, Any]:
    return copy.deepcopy(get_scenario(TRAVEL))


class TestSchedulerEngine:
    def test_linear_chain(self, project_full: dict[str, Any]) -> None:
        scenario = project_full
        report = generate_recovery_schedule(scenario)
        assert report["feasible"] is True
        assert report["outcome"] == "scheduled"
        assert report["unit"] == "duration_units"
        assert report["execution_order"][:3] == [
            "payment-api",
            "vendor-delivery",
            "frontend-integration",
        ]
        backend = next(
            e for e in report["schedule"] if e["entity_id"] == "backend-integration"
        )
        assert backend["hard_prerequisites"] == ["payment-api", "vendor-delivery"]

    def test_parallel_tasks(self, project_full: dict[str, Any]) -> None:
        report = generate_recovery_schedule(project_full)
        layer1 = next(g for g in report["parallel_groups"] if g["layer"] == 1)
        assert set(layer1["entity_ids"]) == {
            "payment-api",
            "vendor-delivery",
            "frontend-integration",
            "unit-testing",
            "release-notes",
        }

    def test_branching(self, project_full: dict[str, Any]) -> None:
        report = generate_recovery_schedule(project_full)
        cp = report["critical_path"]["path"]
        assert "payment-api" in cp
        assert "backend-integration" in cp
        assert "integration-testing" in cp

    def test_multiple_prereqs(self, project_full: dict[str, Any]) -> None:
        report = generate_recovery_schedule(project_full)
        backend = next(
            e for e in report["schedule"] if e["entity_id"] == "backend-integration"
        )
        assert len(backend["hard_prerequisites"]) == 2

    def test_completed_task_preservation(self, project_full: dict[str, Any]) -> None:
        report = generate_recovery_schedule(project_full)
        auth = next(e for e in report["schedule"] if e["entity_id"] == "auth-service")
        assert auth["group"] == "completed"
        assert auth["earliest_start"] == 0.0
        assert auth["earliest_finish"] == 0.0
        assert auth["status"] == "done"

    def test_disruption_with_blocked_and_unaffected(self, project_full: dict[str, Any]) -> None:
        changes = [
            {
                "action": "remove_dependency",
                "dependent": "backend-integration",
                "prerequisite": "vendor-delivery",
            }
        ]
        report = generate_recovery_schedule(
            project_full,
            changes=changes,
            disrupted_entity_id="vendor-delivery",
        )
        assert report["feasible"] is True
        assert report["disrupted_entity_id"] == "vendor-delivery"
        assert "vendor-delivery" in report["groups"]["blocked"]
        assert "backend-integration" in report["groups"]["ready_now"]
        assert report["comparison"]["newly_unblocked"]

    def test_valid_dependency_removal(self, project_full: dict[str, Any]) -> None:
        changes = [
            {
                "action": "remove_dependency",
                "dependent": "backend-integration",
                "prerequisite": "vendor-delivery",
            }
        ]
        report = generate_recovery_schedule(project_full, changes=changes)
        backend = next(
            e for e in report["schedule"] if e["entity_id"] == "backend-integration"
        )
        assert "vendor-delivery" not in backend["hard_prerequisites"]
        assert report["baseline"] is not None
        assert report["comparison"] is not None

    def test_cycle_creation(self, demo_plan: dict[str, Any]) -> None:
        report = generate_recovery_schedule(demo_plan, changes=[CYCLE_CHANGE])
        assert report["feasible"] is False
        assert report["outcome"] == "infeasible"
        assert report["schedule"] is None
        assert report["comparison"] is None

    def test_invalid_reference(self, demo_plan: dict[str, Any]) -> None:
        report = generate_recovery_schedule(demo_plan, changes=[INVALID_REF_CHANGE])
        # apply_changes drops unknown edges silently; the graph remains structurally valid.
        assert report["feasible"] is True
        assert report["outcome"] == "insufficient_duration_data"

    def test_hard_constraint_violation(self, demo_plan: dict[str, Any]) -> None:
        report = generate_recovery_schedule(demo_plan, changes=[HARD_CONSTRAINT_VIOLATION])
        assert report["feasible"] is False
        assert report["outcome"] == "infeasible"

    def test_missing_durations(self, demo_plan: dict[str, Any]) -> None:
        report = generate_recovery_schedule(demo_plan)
        assert report["outcome"] == "insufficient_duration_data"
        assert report["unit"] is None
        assert all(e["earliest_start"] is None for e in report["schedule"] if e["status"] != "done")
        assert report["missing_data"]

    def test_mixed_invalid_durations(self, project_full: dict[str, Any]) -> None:
        bad_cfg = {"durations": {"payment-api": -1, "nope": 5}}
        with pytest.raises(SchedulerError):
            generate_recovery_schedule(project_full, scheduling_config=bad_cfg)

    def test_determinism(self, project_full: dict[str, Any]) -> None:
        random.seed(42)
        a = generate_recovery_schedule(project_full)
        b = generate_recovery_schedule(project_full)
        assert a["execution_order"] == b["execution_order"]
        assert a["schedule"] == b["schedule"]

    def test_no_mutation(self, project_full: dict[str, Any]) -> None:
        original = copy.deepcopy(project_full)
        changes = [
            {
                "action": "remove_dependency",
                "dependent": "backend-integration",
                "prerequisite": "vendor-delivery",
            }
        ]
        generate_recovery_schedule(project_full, changes=changes)
        assert project_full == original

    def test_baseline_vs_recovery_comparison(self, project_full: dict[str, Any]) -> None:
        changes = [
            {
                "action": "remove_dependency",
                "dependent": "backend-integration",
                "prerequisite": "vendor-delivery",
            }
        ]
        report = generate_recovery_schedule(project_full, changes=changes)
        assert report["baseline"] is not None
        assert report["comparison"] is not None
        assert "baseline_completion_duration" in report["comparison"]
        assert "proposed_completion_duration" in report["comparison"]

    def test_infeasible_never_recommended(self, demo_plan: dict[str, Any]) -> None:
        report = generate_recovery_schedule(demo_plan, changes=[CYCLE_CHANGE])
        assert report["feasible"] is False
        assert report["execution_order"] is None
        assert report["groups"] is None

    def test_minutes_buffer_honored(self, travel_plan: dict[str, Any]) -> None:
        cfg = {"duration_unit": "minutes"}
        report = generate_recovery_schedule(travel_plan, scheduling_config=cfg)
        transfer = next(e for e in report["schedule"] if e["entity_id"] == "transfer")
        leg1 = next(e for e in report["schedule"] if e["entity_id"] == "leg-1")
        assert transfer["earliest_start"] == leg1["earliest_finish"] + 30

    def test_invalid_config(self, project_full: dict[str, Any]) -> None:
        with pytest.raises(SchedulerError):
            generate_recovery_schedule(project_full, scheduling_config={"duration_unit": 123})
        with pytest.raises(SchedulerError):
            generate_recovery_schedule(project_full, scheduling_config={"unknown": 1})
        with pytest.raises(SchedulerError):
            generate_recovery_schedule(project_full, scheduling_config="bad")
        with pytest.raises(SchedulerError):
            generate_recovery_schedule(project_full, scheduling_config={"durations": "bad"})
