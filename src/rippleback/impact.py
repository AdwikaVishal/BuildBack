"""Deterministic disruption simulation and dependency-impact analysis.

Phase 2, first slice. This module is **pure**: it never mutates the committed
scenario, never increments its version, and produces identical output for
identical input.

Model
-----
A dependency ``{"dependent": D, "prerequisite": P}`` means *D is completed only
after P is completed* (``P`` is required by ``D``). If an entity becomes
unavailable, everything that (transitively) requires it can no longer proceed.

Given a disrupted target entity:

* The target itself is ``blocked``.
* **Directly affected** entities are those whose ``prerequisite`` is the target
  (one hop away, following dependent edges).
* **Transitively affected** entities are those reachable in two or more hops.
* Everything else is **unaffected** and keeps its committed status, so
  independent work (e.g. ``frontend-integration``) stays healthy.

Affected entities are classified with the existing entity status ``blocked``
(see :data:`rippleback.scenarios.VALID_ENTITY_STATUSES`); no new statuses are
invented. Each affected entity carries an ``evidence_path``: the entity chain
from the disrupted target to that entity.
"""

from __future__ import annotations

from collections import deque
from typing import Any

VALID_SEVERITIES = ("low", "medium", "high", "critical")
DEFAULT_SEVERITY = "high"

AFFECTED_STATE = "blocked"


class ImpactError(ValueError):
    """Raised for invalid disruption requests (unknown entity, bad details)."""


def _dependents_by_prerequisite(scenario: dict[str, Any]) -> dict[str, list[str]]:
    """Map each prerequisite entity id to the ids that directly require it."""
    dependents: dict[str, list[str]] = {}
    for dep in scenario["dependencies"]:
        dependents.setdefault(dep["prerequisite"], []).append(dep["dependent"])
    return dependents


def _evidence_paths(target_id: str, dependents: dict[str, list[str]]) -> dict[str, list[str]]:
    """BFS from the target along dependent edges.

    Returns a shortest evidence path (list of entity ids) for every reachable
    entity, including the target itself (``[target_id]``). Ordering is made
    deterministic by sorting dependents at each step.
    """
    paths: dict[str, list[str]] = {target_id: [target_id]}
    queue: deque[str] = deque([target_id])
    while queue:
        current = queue.popleft()
        for dependent in sorted(set(dependents.get(current, []))):
            if dependent not in paths:
                paths[dependent] = [*paths[current], dependent]
                queue.append(dependent)
    return paths


def simulate_disruption(
    scenario: dict[str, Any],
    *,
    entity_id: str,
    reason: str,
    severity: str = DEFAULT_SEVERITY,
) -> dict[str, Any]:
    """Return a hypothetical impact report for disrupting ``entity_id``.

    The committed ``scenario`` is never modified. Unknown entities, an empty
    reason, or an unsupported severity raise :class:`ImpactError`.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ImpactError("reason must be a non-empty string")
    if severity not in VALID_SEVERITIES:
        raise ImpactError(
            f"unknown severity {severity!r}; allowed: {list(VALID_SEVERITIES)}"
        )

    entities: list[dict[str, Any]] = scenario["entities"]
    by_id = {entity["id"]: entity for entity in entities}
    if entity_id not in by_id:
        raise ImpactError(
            f"unknown entity_id {entity_id!r}; available: {sorted(by_id)}"
        )

    dependents = _dependents_by_prerequisite(scenario)
    paths = _evidence_paths(entity_id, dependents)
    direct_ids = set(dependents.get(entity_id, []))

    directly_affected: list[dict[str, Any]] = []
    transitively_affected: list[dict[str, Any]] = []
    unaffected: list[dict[str, Any]] = []

    for entity in entities:  # iterate in scenario order for deterministic output
        eid = entity["id"]
        if eid == entity_id:
            continue
        if eid in paths:
            entry = {
                "entity_id": eid,
                "name": entity["name"],
                "state": AFFECTED_STATE,
                "evidence_path": paths[eid],
            }
            if eid in direct_ids:
                directly_affected.append(entry)
            else:
                transitively_affected.append(entry)
        else:
            unaffected.append(
                {"entity_id": eid, "name": entity["name"], "state": entity["status"]}
            )

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "disruption": {
            "entity_id": entity_id,
            "name": by_id[entity_id]["name"],
            "state": AFFECTED_STATE,
            "reason": reason.strip(),
            "severity": severity,
        },
        "directly_affected": directly_affected,
        "transitively_affected": transitively_affected,
        "unaffected": unaffected,
    }
