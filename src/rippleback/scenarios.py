"""Seeded project scenarios for RippleBack.

Concepts
--------
- **Entity**: a node in the project plan — one piece of work with a stable
  id, a human-readable name, and a status (e.g. ``payment-api``).
- **Dependency**: a directed edge between two entities. Each dependency is
  stored as ``{"dependent": <entity that needs work done>, "prerequisite":
  <entity that must come first>}``. Example: ``backend-integration`` depends
  on ``payment-api`` (payment-api is *required by* backend-integration).
- **Resource** (optional): a synthetic work performer with a non-negative
  integer ``capacity`` in abstract ``work_units``. An entity may name an
  ``assignee`` (a resource id) and an integer ``effort`` (also ``work_units``).

Constraints are simple, declarative rules about the plan. Phase 2 scenarios
are healthy: status ``healthy``, no disruptions. Resource/assignment data is
optional; only ``demo-release-plan-staffed`` carries it, and its values are
synthetic demo data, not real capacity.

This module is the single source of truth for seeded scenarios; the MCP tool
in :mod:`rippleback.server` only reads from it.
"""

from __future__ import annotations

import copy
from typing import Any

from rippleback.graph import KNOWN_EDGE_TYPES, detect_cycle

VALID_ENTITY_STATUSES = frozenset({"pending", "in_progress", "done", "blocked"})
REQUIRED_ENTITY_FIELDS = frozenset({"id", "name", "status"})
REQUIRED_DEPENDENCY_FIELDS = frozenset({"dependent", "prerequisite"})
REQUIRED_CONSTRAINT_FIELDS = frozenset({"type", "entity_ids"})
REQUIRED_RESOURCE_FIELDS = frozenset({"id", "name", "capacity"})
REQUIRED_SCENARIO_FIELDS = frozenset(
    {"scenario_id", "plan_id", "version", "entities", "dependencies", "constraints"}
)


class ScenarioError(ValueError):
    """Raised for unknown scenario ids or malformed scenario data."""


def _demo_release_plan() -> dict[str, Any]:
    """Healthy payment-api release plan (the Phase 2 seed scenario)."""
    return {
        "scenario_id": "demo-release-plan",
        "plan_id": "plan-payment-api-release",
        "version": 1,
        "status": "healthy",
        "entities": [
            {"id": "payment-api", "name": "Payment API service", "status": "done"},
            {"id": "backend-integration", "name": "Backend integration", "status": "done"},
            {"id": "integration-testing", "name": "Integration testing", "status": "done"},
            {"id": "frontend-integration", "name": "Frontend integration", "status": "in_progress"},
            {"id": "release-validation", "name": "Release validation", "status": "pending"},
            {"id": "production-release", "name": "Production release", "status": "pending"},
        ],
        "dependencies": [
            {"dependent": "backend-integration", "prerequisite": "payment-api"},
            {"dependent": "integration-testing", "prerequisite": "backend-integration"},
            {"dependent": "release-validation", "prerequisite": "integration-testing"},
            {"dependent": "production-release", "prerequisite": "release-validation"},
        ],
        "constraints": [
            {
                "type": "all_entities_done_before",
                "entity_ids": ["production-release"],
                "description": "Every other entity must be done before production-release.",
            },
        ],
    }


def _demo_release_plan_staffed() -> dict[str, Any]:
    """Staffed variant of the demo release plan (Phase 5, synthetic demo data).

    The same entities and dependencies as ``demo-release-plan``, plus an explicit
    ``resources`` list and per-entity ``assignee``/``effort`` values. **All
    numbers are synthetic demo values in an abstract ``work_units`` scale — they
    are not real team capacity, effort, or availability.**

    Chosen values (capacity and effort in ``work_units``):
    - ``alice`` (capacity 3) is assigned the finished payment-chain work plus the
      unfinished ``release-validation`` (3) and ``production-release`` (1) — an
      unfinished load of 4 > 3, so Alice is **overloaded** by 1 unit.
    - ``bob`` (capacity 3) is assigned ``frontend-integration`` (2), leaving 1
      unit free — enough to accept ``production-release`` (1).
    """
    scenario = _demo_release_plan()
    scenario["scenario_id"] = "demo-release-plan-staffed"
    scenario["plan_id"] = "plan-payment-api-release-staffed"
    scenario["resources"] = [
        {"id": "alice", "name": "Alice (synthetic)", "capacity": 3},
        {"id": "bob", "name": "Bob (synthetic)", "capacity": 3},
    ]
    assignee_by_id = {
        "payment-api": ("alice", 2),
        "backend-integration": ("alice", 2),
        "integration-testing": ("alice", 1),
        "frontend-integration": ("bob", 2),
        "release-validation": ("alice", 3),
        "production-release": ("alice", 1),
    }
    for entity in scenario["entities"]:
        entity["assignee"], entity["effort"] = assignee_by_id[entity["id"]]
    return scenario


def _project_full() -> dict[str, Any]:
    """Extended project scenario for blocker triage (Phase 6).

    Same payment-API chain as ``demo-release-plan`` but seeded with the fields
    blocker triage needs to demonstrate all five states:

    * ``auth-service`` is ``done`` (independent finished work).
    * the payment chain is unfinished, so an outage cascades to ``blocked``.
    * ``frontend-integration`` is independent -> ``healthy``.
    * ``unit-testing`` ``benefits_from`` ``integration-testing`` (a **soft** edge)
      -> ``at_risk`` when integration is blocked.
    * ``release-notes`` has only 30 minutes of slack -> ``at_risk`` by low slack.
    * ``vendor-delivery`` is ``metadata.external`` (outside the team), so a delay
      on it makes its dependents ``external``.

    ``duration`` (relative units) and ``resources``/``assignee``/``effort`` are
    synthetic demo data, not real durations, capacity, or effort.
    """
    return {
        "scenario_id": "project-full",
        "plan_id": "plan-payment-api-release-full",
        "version": 1,
        "status": "healthy",
        "entities": [
            {
                "id": "auth-service",
                "name": "Auth service",
                "status": "done",
                "duration": 2,
                "assignee": "alice",
                "effort": 1,
            },
            {
                "id": "payment-api",
                "name": "Payment API service",
                "status": "in_progress",
                "duration": 2,
                "assignee": "alice",
                "effort": 2,
            },
            {
                "id": "vendor-delivery",
                "name": "Vendor deliverable",
                "status": "pending",
                "duration": 1,
                "assignee": "alice",
                "effort": 1,
                "metadata": {"external": True},
            },
            {
                "id": "backend-integration",
                "name": "Backend integration",
                "status": "pending",
                "duration": 3,
                "assignee": "alice",
                "effort": 2,
            },
            {
                "id": "integration-testing",
                "name": "Integration testing",
                "status": "pending",
                "duration": 2,
                "assignee": "alice",
                "effort": 1,
            },
            {
                "id": "release-validation",
                "name": "Release validation",
                "status": "pending",
                "duration": 1,
                "assignee": "alice",
                "effort": 3,
            },
            {
                "id": "production-release",
                "name": "Production release",
                "status": "pending",
                "duration": 1,
                "assignee": "alice",
                "effort": 1,
            },
            {
                "id": "frontend-integration",
                "name": "Frontend integration",
                "status": "in_progress",
                "duration": 2,
                "assignee": "bob",
                "effort": 2,
            },
            {
                "id": "unit-testing",
                "name": "Unit testing",
                "status": "pending",
                "duration": 1,
                "assignee": "bob",
                "effort": 1,
            },
            {
                "id": "release-notes",
                "name": "Release notes",
                "status": "pending",
                "duration": 1,
                "assignee": "bob",
                "effort": 1,
                "metadata": {"slack_minutes": 30},
            },
        ],
        "resources": [
            {"id": "alice", "name": "Alice (synthetic)", "capacity": 3},
            {"id": "bob", "name": "Bob (synthetic)", "capacity": 3},
        ],
        "dependencies": [
            {"dependent": "backend-integration", "prerequisite": "payment-api"},
            {"dependent": "backend-integration", "prerequisite": "vendor-delivery"},
            {"dependent": "integration-testing", "prerequisite": "backend-integration"},
            {"dependent": "release-validation", "prerequisite": "integration-testing"},
            {"dependent": "production-release", "prerequisite": "release-validation"},
            {
                "dependent": "unit-testing",
                "prerequisite": "integration-testing",
                "dependency_type": "benefits_from",
            },
        ],
        "constraints": [
            {
                "type": "all_entities_done_before",
                "entity_ids": ["production-release"],
                "description": "Every other entity must be done before production-release.",
            },
        ],
    }


def _travel() -> dict[str, Any]:
    """Travel scenario reused on the same engine (Phase 6).

    A delayed first train leg with explicit connection buffers (``slack_minutes``
    on the dependent) lets the engine show that downstream legs still proceed
    when a buffer absorbs the delay, instead of assuming every downstream event
    fails. Dependencies use ``requires_arrival_by``; durations and slack are
    synthetic demo data.
    """
    return {
        "scenario_id": "travel",
        "plan_id": "plan-train-connection",
        "version": 1,
        "status": "healthy",
        "entities": [
            {"id": "origin", "name": "Origin station", "status": "done", "duration": 5},
            {
                "id": "leg-1",
                "name": "Train leg 1",
                "status": "in_progress",
                "duration": 120,
            },
            {
                "id": "transfer",
                "name": "Station transfer",
                "status": "pending",
                "duration": 10,
                "metadata": {"slack_minutes": 30},
            },
            {
                "id": "connecting-leg",
                "name": "Connecting train",
                "status": "pending",
                "duration": 90,
                "metadata": {"slack_minutes": 20},
            },
            {
                "id": "hotel-check-in",
                "name": "Hotel check-in",
                "status": "pending",
                "duration": 20,
                "metadata": {"slack_minutes": 90},
            },
            {
                "id": "meeting",
                "name": "Client meeting",
                "status": "pending",
                "duration": 60,
                "metadata": {"slack_minutes": 120},
            },
            {
                "id": "dinner-reservation",
                "name": "Dinner reservation",
                "status": "pending",
                "duration": 45,
            },
        ],
        "dependencies": [
            {
                "dependent": "transfer",
                "prerequisite": "leg-1",
                "dependency_type": "requires_arrival_by",
                "constraint_parameters": {"required_buffer_minutes": 30},
            },
            {
                "dependent": "connecting-leg",
                "prerequisite": "transfer",
                "dependency_type": "requires_arrival_by",
            },
            {
                "dependent": "hotel-check-in",
                "prerequisite": "connecting-leg",
                "dependency_type": "requires_arrival_by",
            },
            {
                "dependent": "meeting",
                "prerequisite": "hotel-check-in",
                "dependency_type": "requires_arrival_by",
            },
        ],
        "constraints": [],
    }


SCENARIOS: dict[str, dict[str, Any]] = {
    "demo-release-plan": _demo_release_plan(),
    "demo-release-plan-staffed": _demo_release_plan_staffed(),
    "project-full": _project_full(),
    "travel": _travel(),
}


def validate_scenario(data: Any) -> list[str]:
    """Return a list of validation errors for a scenario dict (empty if valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"scenario must be a dict, got {type(data).__name__}"]

    missing = REQUIRED_SCENARIO_FIELDS - data.keys()
    if missing:
        errors.append(f"missing scenario fields: {sorted(missing)}")
        return errors

    if not isinstance(data["scenario_id"], str) or not data["scenario_id"].strip():
        errors.append("scenario_id must be a non-empty string")
    if not isinstance(data["plan_id"], str) or not data["plan_id"].strip():
        errors.append("plan_id must be a non-empty string")
    if not isinstance(data["version"], int) or isinstance(data["version"], bool):
        errors.append("version must be an integer")

    resources = data.get("resources")
    resource_ids: set[str] = set()
    if resources is not None:
        if not isinstance(resources, list):
            errors.append("resources must be a list")
        else:
            for i, resource in enumerate(resources):
                if not isinstance(resource, dict):
                    errors.append(f"resources[{i}] must be a dict")
                    continue
                missing_resource = REQUIRED_RESOURCE_FIELDS - resource.keys()
                if missing_resource:
                    errors.append(
                        f"resources[{i}] missing fields: {sorted(missing_resource)}"
                    )
                    continue
                rid = resource["id"]
                if not isinstance(rid, str) or not rid.strip():
                    errors.append(f"resources[{i}].id must be a non-empty string")
                    continue
                if rid in resource_ids:
                    errors.append(f"duplicate resource id: {rid!r}")
                resource_ids.add(rid)
                if (
                    not isinstance(resource["name"], str)
                    or not resource["name"].strip()
                ):
                    errors.append(f"resources[{i}].name must be a non-empty string")
                capacity = resource["capacity"]
                if (
                    not isinstance(capacity, int)
                    or isinstance(capacity, bool)
                    or capacity < 0
                ):
                    errors.append(
                        f"resources[{i}].capacity must be a non-negative integer"
                    )

    entities = data["entities"]
    if not isinstance(entities, list) or not entities:
        errors.append("entities must be a non-empty list")
        entity_ids: set[str] = set()
    else:
        entity_ids = set()
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                errors.append(f"entities[{i}] must be a dict")
                continue
            missing_entity = REQUIRED_ENTITY_FIELDS - entity.keys()
            if missing_entity:
                errors.append(f"entities[{i}] missing fields: {sorted(missing_entity)}")
                continue
            eid = entity["id"]
            if not isinstance(eid, str) or not eid.strip():
                errors.append(f"entities[{i}].id must be a non-empty string")
                continue
            if eid in entity_ids:
                errors.append(f"duplicate entity id: {eid!r}")
            entity_ids.add(eid)
            if entity["status"] not in VALID_ENTITY_STATUSES:
                errors.append(
                    f"entities[{i}].status {entity['status']!r} "
                    f"not in {sorted(VALID_ENTITY_STATUSES)}"
                )
            if "assignee" in entity:
                assignee = entity["assignee"]
                if not isinstance(assignee, str) or not assignee.strip():
                    errors.append(
                        f"entities[{i}].assignee must be a non-empty string"
                    )
                elif assignee not in resource_ids:
                    errors.append(
                        f"entities[{i}].assignee references unknown resource "
                        f"{assignee!r}"
                    )
            if "effort" in entity:
                effort = entity["effort"]
                if (
                    not isinstance(effort, int)
                    or isinstance(effort, bool)
                    or effort < 0
                ):
                    errors.append(
                        f"entities[{i}].effort must be a non-negative integer"
                    )
            if "duration" in entity:
                duration = entity["duration"]
                if (
                    not isinstance(duration, (int, float))
                    or isinstance(duration, bool)
                    or duration <= 0
                ):
                    errors.append(
                        f"entities[{i}].duration must be a positive number"
                    )
            if "metadata" in entity and not isinstance(entity["metadata"], dict):
                errors.append(f"entities[{i}].metadata must be a dict")

    dependencies = data["dependencies"]
    if not isinstance(dependencies, list):
        errors.append("dependencies must be a list")
    else:
        seen_pairs: set[tuple[str, str]] = set()
        cycle_edges: list[tuple[str, str]] = []
        for i, dep in enumerate(dependencies):
            if not isinstance(dep, dict):
                errors.append(f"dependencies[{i}] must be a dict")
                continue
            missing_dep = REQUIRED_DEPENDENCY_FIELDS - dep.keys()
            if missing_dep:
                errors.append(f"dependencies[{i}] missing fields: {sorted(missing_dep)}")
                continue
            dependent, prereq = dep["dependent"], dep["prerequisite"]
            if not isinstance(dependent, str) or not isinstance(prereq, str):
                errors.append(f"dependencies[{i}] values must be strings")
                continue
            for ref in (dependent, prereq):
                if entity_ids and ref not in entity_ids:
                    errors.append(f"dependencies[{i}] references unknown entity {ref!r}")
            if dependent == prereq:
                errors.append(f"dependencies[{i}] is a self-dependency on {dependent!r}")
            if "dependency_type" in dep and dep["dependency_type"] not in KNOWN_EDGE_TYPES:
                errors.append(
                    f"dependencies[{i}].dependency_type {dep['dependency_type']!r} "
                    f"not in {sorted(KNOWN_EDGE_TYPES)}"
                )
            if "constraint_parameters" in dep and not isinstance(
                dep["constraint_parameters"], dict
            ):
                errors.append(f"dependencies[{i}].constraint_parameters must be a dict")
            pair = (dependent, prereq)
            if pair in seen_pairs:
                errors.append(f"duplicate dependency: {pair}")
            seen_pairs.add(pair)
            cycle_edges.append((prereq, dependent))

        cycle = detect_cycle(cycle_edges)
        if cycle is not None:
            errors.append(f"dependency cycle detected: {' -> '.join(cycle)}")

    constraints = data["constraints"]
    if not isinstance(constraints, list):
        errors.append("constraints must be a list")
    else:
        for i, constraint in enumerate(constraints):
            if not isinstance(constraint, dict):
                errors.append(f"constraints[{i}] must be a dict")
                continue
            missing_c = REQUIRED_CONSTRAINT_FIELDS - constraint.keys()
            if missing_c:
                errors.append(f"constraints[{i}] missing fields: {sorted(missing_c)}")
                continue
            if not isinstance(constraint["type"], str) or not constraint["type"].strip():
                errors.append(f"constraints[{i}].type must be a non-empty string")
            ids = constraint["entity_ids"]
            if not isinstance(ids, list) or not ids:
                errors.append(f"constraints[{i}].entity_ids must be a non-empty list")
            else:
                for ref in ids:
                    if entity_ids and isinstance(ref, str) and ref not in entity_ids:
                        errors.append(
                            f"constraints[{i}] references unknown entity {ref!r}"
                        )

    return errors


def get_scenario(scenario_id: str) -> dict[str, Any]:
    """Look up and re-validate a seeded scenario by id.

    Raises:
        ScenarioError: if the id is empty, unknown, or its data is malformed.
    """
    if not scenario_id or not scenario_id.strip():
        raise ScenarioError("scenario_id must be a non-empty string")
    key = scenario_id.strip()
    data = SCENARIOS.get(key)
    if data is None:
        raise ScenarioError(
            f"unknown scenario_id {key!r}; available: {sorted(SCENARIOS)}"
        )
    errors = validate_scenario(data)
    if errors:
        raise ScenarioError(f"malformed scenario {key!r}: {'; '.join(errors)}")
    return copy.deepcopy(data)
