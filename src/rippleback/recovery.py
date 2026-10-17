"""Deterministic recovery alternatives for a disrupted dependency.

Phase 3, first slice. This module is **pure**: it never mutates the committed
scenario, never changes entity statuses or dependencies, and never increments
the plan version. Identical input yields equivalent options in the same order.

Semantics (chosen to avoid inventing a second graph)
----------------------------------------------------
An "alternative dependency" is a *modified view of the existing dependency set*
in which the broken edge is removed. Given a disrupted prerequisite entity, we
look at every dependency edge whose ``prerequisite`` is that entity and propose
bypassing it so the ``dependent`` entity can proceed.

Only direct edges produce options.

``unblocks`` definition
-----------------------
For a proposed removal of edge ``dependent -> entity_id``, build the *residual*
graph: the committed dependencies with that one edge removed. ``unblocks`` is
every entity that is affected by the disruption in the committed graph but is
**no longer reachable** from the disrupted ``entity_id`` in the residual graph.
In other words it is a structural claim -- "the disruption's dependency path to
this entity has been removed" -- not an inference from reachability in the
original graph. An entity that still depends on the disrupted prerequisite
through another path, or on another prerequisite that is itself still blocked,
is deliberately *not* listed. Output is in scenario entity order.

Because the model treats ``entity_id`` as the only unavailable entity,
"unreachable from ``entity_id`` in the residual graph" means "no remaining
prerequisite chain depends on the disrupted entity". With more than one
disruption at once this set would overstate readiness; the engine simulates a
single disruption only. Independent work (e.g. ``frontend-integration``) has no
edge to the disrupted entity and therefore never appears in any option.

The proposed ``alternative.dependencies`` list uses the existing dependency
shape, so consumers need no new graph structure.
"""

from __future__ import annotations

from typing import Any

from rippleback.impact import _dependents_by_prerequisite, _evidence_paths

REMOVE_DEPENDENCY = "remove_dependency"


class RecoveryError(ValueError):
    """Raised for invalid recovery requests (unknown entity)."""


def _option_id(dependent: str, prerequisite: str) -> str:
    """Stable identifier derived from the affected dependency edge."""
    return f"drop-dependency:{dependent}->{prerequisite}"


def find_alternative_dependencies(
    scenario: dict[str, Any],
    *,
    entity_id: str,
) -> dict[str, Any]:
    """Return hypothetical recovery options for disrupting ``entity_id``.

    The committed ``scenario`` is never modified. An unknown ``entity_id``
    raises :class:`RecoveryError`. A valid entity with no dependents yields an
    empty ``options`` list (a legitimate result, not a fallback).
    """
    entities: list[dict[str, Any]] = scenario["entities"]
    by_id = {entity["id"]: entity for entity in entities}
    if entity_id not in by_id:
        raise RecoveryError(
            f"unknown entity_id {entity_id!r}; available: {sorted(by_id)}"
        )

    dependencies: list[dict[str, Any]] = scenario["dependencies"]
    dependents = _dependents_by_prerequisite(scenario)
    affected = _evidence_paths(entity_id, dependents)

    options: list[dict[str, Any]] = []
    for edge in dependencies:  # iterate fixture order for determinism
        if edge["prerequisite"] != entity_id:
            continue
        dependent = edge["dependent"]
        removed = {"dependent": dependent, "prerequisite": entity_id}
        proposed = [dict(dep) for dep in dependencies if dep != edge]

        # Recompute reachability on the residual graph (this edge removed).
        # An entity is unblocked only if it was affected originally and the
        # disruption can no longer reach it. This must not be inferred from the
        # original graph's reachability -- an entity that still depends on the
        # disrupted prerequisite via another path stays blocked.
        residual_dependents = _dependents_by_prerequisite(
            {**scenario, "dependencies": proposed}
        )
        residual = _evidence_paths(entity_id, residual_dependents)
        unblocks = [
            entity["id"]
            for entity in entities
            if entity["id"] in affected and entity["id"] not in residual
        ]

        options.append(
            {
                "option_id": _option_id(dependent, entity_id),
                "description": (
                    f"Bypass the dependency '{dependent}' requires "
                    f"'{entity_id}' so '{dependent}' can proceed without the "
                    f"disrupted prerequisite."
                ),
                "affected_entity_id": dependent,
                "affected_dependency": removed,
                "alternative": {
                    "action": REMOVE_DEPENDENCY,
                    "removed_dependency": removed,
                    "dependencies": proposed,
                },
                "unblocks": unblocks,
                "assumptions": [
                    f"Assumes '{dependent}' does not strictly require "
                    f"'{entity_id}' for this release.",
                    "Assumes the removed prerequisite can be satisfied "
                    "out-of-band or is no longer needed.",
                ],
                "limitations": [
                    "Hypothetical only; the committed plan is not changed.",
                    f"Removing a hard prerequisite may break integration "
                    f"guarantees for '{dependent}'.",
                    "Requires human review before adoption.",
                ],
            }
        )

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "disruption": {"entity_id": entity_id, "name": by_id[entity_id]["name"]},
        "options": options,
    }
