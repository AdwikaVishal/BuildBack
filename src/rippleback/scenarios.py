"""Seeded project scenarios for RippleBack.

Concepts
--------
- **Entity**: a node in the project plan — one piece of work with a stable
  id, a human-readable name, and a status (e.g. ``payment-api``).
- **Dependency**: a directed edge between two entities. Each dependency is
  stored as ``{"dependent": <entity that needs work done>, "prerequisite":
  <entity that must come first>}``. Example: ``backend-integration`` depends
  on ``payment-api`` (payment-api is *required by* backend-integration).

Constraints are simple, declarative rules about the plan. Phase 2 scenarios
are healthy: status ``healthy``, no disruptions.

This module is the single source of truth for seeded scenarios; the MCP tool
in :mod:`rippleback.server` only reads from it.
"""

from __future__ import annotations

import copy
from typing import Any

VALID_ENTITY_STATUSES = frozenset({"pending", "in_progress", "done", "blocked"})
REQUIRED_ENTITY_FIELDS = frozenset({"id", "name", "status"})
REQUIRED_DEPENDENCY_FIELDS = frozenset({"dependent", "prerequisite"})
REQUIRED_CONSTRAINT_FIELDS = frozenset({"type", "entity_ids"})
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


SCENARIOS: dict[str, dict[str, Any]] = {
    "demo-release-plan": _demo_release_plan(),
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

    dependencies = data["dependencies"]
    if not isinstance(dependencies, list):
        errors.append("dependencies must be a list")
    else:
        seen_pairs: set[tuple[str, str]] = set()
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
            pair = (dependent, prereq)
            if pair in seen_pairs:
                errors.append(f"duplicate dependency: {pair}")
            seen_pairs.add(pair)

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
