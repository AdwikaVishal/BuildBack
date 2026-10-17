"""Deterministic, read-only workload rebalancing for a staffed scenario.

Phase 5, first slice. Given a scenario that carries **staffing data** -- a
``resources`` list and per-entity ``assignee``/``effort`` values -- this module
proposes hypothetical reassignments that relieve over-capacity resources.

This module is **pure**: it never mutates the committed scenario, its entity
statuses, assignments, dependency list, or version. Identical input yields an
identical report.

Units and honesty
-----------------
Capacity and effort are integers in one abstract ``work_units`` scale. There is
**no time dimension**: the model has no durations, dates, or schedules, so the
engine never predicts delivery dates and never claims a deadline or forecast
improves. It only reports a structural workload fact: a resource's assigned
unfinished effort exceeds its declared capacity.

Workload definition
-------------------
A resource's assigned workload is the sum of ``effort`` over its entities whose
status is ``pending``, ``in_progress``, or ``blocked`` (i.e. not ``done``).
``done`` entities are excluded — completed work does not consume capacity.
A resource is **overloaded** when that sum exceeds its ``capacity``.

Suggestions are greedy in scenario order: for each overloaded resource, its
unfinished assigned entities are considered in scenario order, and each movable
entity is placed on the first resource (in declared order) with enough free
capacity for that entity's entire effort. This can move more effort than the
strict minimum; that is recorded as a limitation.
"""

from __future__ import annotations

from typing import Any

WORK_UNIT = "work_units"

OUTCOME_OK = "ok"
OUTCOME_INSUFFICIENT_DATA = "insufficient_data"

UNFINISHED_STATUSES = frozenset({"pending", "in_progress", "blocked"})

GLOBAL_ASSUMPTIONS = [
    "Capacity and effort use an abstract 'work_units' scale with no time "
    "dimension; no durations or schedules are modeled.",
    "Any resource is assumed able to perform any task; skills, roles, and "
    "availability are not modeled.",
]

GLOBAL_LIMITATIONS = [
    "Suggestions are greedy in scenario order and may relocate more effort than "
    "the strict minimum needed to clear an overload.",
    "Does not predict or claim any delivery date, deadline, or forecast "
    "improvement.",
    "Read-only: committed assignments, dependencies, statuses, and the plan "
    "version are never changed.",
    "Synthetic demo data; not real team capacity, effort, or availability.",
]


class WorkloadError(ValueError):
    """Raised for invalid rebalancing requests (stale version, unknown resource)."""


def _insufficient_data_report(
    scenario: dict[str, Any], expected_plan_version: int
) -> dict[str, Any]:
    """Structured outcome when a scenario carries no staffing data."""
    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "expected_plan_version": expected_plan_version,
        "simulated": True,
        "unit": WORK_UNIT,
        "outcome": OUTCOME_INSUFFICIENT_DATA,
        "resources": [],
        "suggestions": [],
        "unresolved": [],
        "assumptions": list(GLOBAL_ASSUMPTIONS),
        "limitations": [
            "Scenario provides no staffing data (resources/assignee/effort); "
            "no rebalancing was attempted.",
            *GLOBAL_LIMITATIONS,
        ],
    }


def _resource_summaries(
    resources: list[dict[str, Any]],
    counts: dict[str, int],
    assigned_ids: dict[str, list[str]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for resource in resources:
        rid = resource["id"]
        capacity = resource["capacity"]
        assigned = counts.get(rid, 0)
        free = capacity - assigned
        summaries.append(
            {
                "resource_id": rid,
                "name": resource["name"],
                "capacity": capacity,
                "assigned_effort": assigned,
                "free_capacity": free,
                "overloaded": free < 0,
                "assigned_entity_ids": list(assigned_ids.get(rid, [])),
            }
        )
    return summaries


def rebalance_workload(
    scenario: dict[str, Any],
    *,
    expected_plan_version: int,
    resource_id: str | None = None,
) -> dict[str, Any]:
    """Return deterministic, read-only reassignment suggestions.

    The committed ``scenario`` is never modified. A non-integer or stale
    ``expected_plan_version`` and an unknown ``resource_id`` raise
    :class:`WorkloadError`. A scenario without staffing data returns a
    structured ``insufficient_data`` outcome.

    When ``resource_id`` is supplied, only that resource is treated as a source
    of overload; every resource may still be a destination.
    """
    if not isinstance(expected_plan_version, int) or isinstance(
        expected_plan_version, bool
    ):
        raise WorkloadError("expected_plan_version must be an integer")

    version = scenario["version"]
    if expected_plan_version != version:
        raise WorkloadError(
            f"stale expected_plan_version {expected_plan_version}; committed "
            f"plan version is {version}"
        )

    resources: list[dict[str, Any]] = scenario.get("resources") or []
    if not resources:
        return _insufficient_data_report(scenario, expected_plan_version)

    by_resource = {resource["id"]: resource for resource in resources}
    if resource_id is not None and resource_id not in by_resource:
        raise WorkloadError(
            f"unknown resource_id {resource_id!r}; available: {sorted(by_resource)}"
        )

    entities: list[dict[str, Any]] = scenario["entities"]
    counts: dict[str, int] = {}
    assigned_ids: dict[str, list[str]] = {}
    movable: list[tuple[str, str, int]] = []
    for entity in entities:  # scenario order for determinism
        if entity["status"] not in UNFINISHED_STATUSES:
            continue
        assignee = entity.get("assignee")
        effort = entity.get("effort")
        if assignee is None or effort is None or assignee not in by_resource:
            continue
        counts[assignee] = counts.get(assignee, 0) + effort
        assigned_ids.setdefault(assignee, []).append(entity["id"])
        movable.append((entity["id"], assignee, effort))

    free = {r["id"]: r["capacity"] - counts.get(r["id"], 0) for r in resources}
    moved: set[str] = set()
    suggestions: list[dict[str, Any]] = []

    for source in resources:  # declared order for determinism
        sid = source["id"]
        if resource_id is not None and sid != resource_id:
            continue
        if free[sid] >= 0:
            continue
        for entity_id, owner, effort in movable:
            if owner != sid or entity_id in moved or free[sid] >= 0:
                continue
            destination = next(
                (
                    candidate["id"]
                    for candidate in resources
                    if candidate["id"] != sid and free[candidate["id"]] >= effort
                ),
                None,
            )
            if destination is None:
                continue
            free[sid] += effort
            free[destination] -= effort
            moved.add(entity_id)
            suggestions.append(
                {
                    "suggestion_id": f"rebalance:{entity_id}->{destination}",
                    "entity_id": entity_id,
                    "effort": effort,
                    "from_resource_id": sid,
                    "to_resource_id": destination,
                    "reason": (
                        f"Resource '{sid}' is over capacity "
                        f"({counts.get(sid, 0)} > {source['capacity']} {WORK_UNIT}); "
                        f"'{entity_id}' ({effort} {WORK_UNIT}) is reassigned to "
                        f"'{destination}', which has {free[destination] + effort} "
                        f"{WORK_UNIT} free."
                    ),
                    "assumptions": [
                        f"Assumes resource '{destination}' can accept "
                        f"'{entity_id}'; skills and availability are not modeled."
                    ],
                    "limitations": [
                        "Hypothetical only; committed assignments are not changed.",
                        "Does not assert any deadline or delivery date improves.",
                    ],
                }
            )

    unresolved: list[dict[str, Any]] = []
    for source in resources:
        sid = source["id"]
        if resource_id is not None and sid != resource_id:
            continue
        if free[sid] >= 0:
            continue
        for entity_id, owner, effort in movable:
            if owner == sid and entity_id not in moved:
                unresolved.append(
                    {
                        "entity_id": entity_id,
                        "resource_id": sid,
                        "effort": effort,
                        "reason": (
                            f"no other resource has enough free capacity for "
                            f"{effort} {WORK_UNIT}."
                        ),
                    }
                )

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": version,
        "expected_plan_version": expected_plan_version,
        "simulated": True,
        "unit": WORK_UNIT,
        "outcome": OUTCOME_OK,
        "resources": _resource_summaries(resources, counts, assigned_ids),
        "suggestions": suggestions,
        "unresolved": unresolved,
        "assumptions": list(GLOBAL_ASSUMPTIONS),
        "limitations": list(GLOBAL_LIMITATIONS),
    }
