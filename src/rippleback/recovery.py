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

``find_substitute_options`` (Milestone 5)
-----------------------------------------
A sibling engine that proposes **substitute-service** options for a disrupted
prerequisite without changing anything above. A candidate exists only when the
disrupted entity explicitly declares ``metadata.substitutes`` -- a list of
entity ids in the same scenario. That key is the only place the current schema
can carry substitution data: entity ``metadata`` is a validated freeform dict
(unknown keys allowed), and no shipped fixture declares it, so every shipped
scenario returns the documented ``no_substitute_candidates`` result with
``missing_data`` explaining exactly what is absent. Nothing is inferred from
names, statuses, resources, or graph structure, and no service, availability,
compatibility, cost, or risk is ever invented.

Each declared candidate becomes one option (``substitute:{prerequisite}->{substitute}``)
whose change set reroutes **every** dependency edge on the disrupted
prerequisite -- one ``remove_dependency`` plus one ``add_dependency`` per
dependent, preserving each edge's ``dependency_type`` and
``constraint_parameters`` -- and is validated with
:func:`rippleback.constraints.validate_hard_constraints` (which includes graph
validation). Options with invalid, cyclic, self-referential, unknown, or
constraint-breaking change sets are reported ``feasible: False`` with their
stable violation codes and rejection reasons; they are never presented as
usable. The proposed options carry the standard ``changes`` list, so they flow
through the forecast, scoring, and comparison engines with no special cases.
"""

from __future__ import annotations

import copy
from typing import Any

from rippleback.constraints import validate_hard_constraints
from rippleback.impact import _dependents_by_prerequisite, _evidence_paths

REMOVE_DEPENDENCY = "remove_dependency"
ADD_DEPENDENCY = "add_dependency"

#: Result outcomes for :func:`find_substitute_options`.
OUTCOME_SUBSTITUTES_PROPOSED = "substitutes_proposed"
OUTCOME_NO_SUBSTITUTE_CANDIDATES = "no_substitute_candidates"
OUTCOME_NO_FEASIBLE_SUBSTITUTE = "no_feasible_substitute"

#: Entity metadata key declaring substitute candidates (Milestone 5).
SUBSTITUTES_METADATA_KEY = "substitutes"


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


_GATE_FAILURE_REASON = (
    "the proposed change set fails hard-constraint and graph validation; see "
    "'violations' for the stable codes and messages."
)


def _substitute_option_id(prerequisite: str, substitute: str) -> str:
    """Stable identifier for a substitute of ``prerequisite``."""
    return f"substitute:{prerequisite}->{substitute}"


def _declared_substitutes(entity: dict[str, Any]) -> list[str] | None:
    """Parse ``entity.metadata.substitutes``; ``None`` when undeclared.

    The declared value must be a list of strings; duplicates are removed,
    keeping the first occurrence, so option ids stay unique.

    Raises:
        RecoveryError: when the key is present but malformed (not a list, or
            an entry that is not a string entity id).
    """
    metadata = entity.get("metadata", {})
    if SUBSTITUTES_METADATA_KEY not in metadata:
        return None
    raw = metadata[SUBSTITUTES_METADATA_KEY]
    if not isinstance(raw, list):
        raise RecoveryError(
            f"entity {entity['id']!r}: "
            f"metadata.{SUBSTITUTES_METADATA_KEY} must be a list of entity ids, "
            f"got {type(raw).__name__}"
        )
    seen: set[str] = set()
    unique: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise RecoveryError(
                f"entity {entity['id']!r}: "
                f"metadata.{SUBSTITUTES_METADATA_KEY}[{index}] must be a string "
                f"entity id, got {type(value).__name__}"
            )
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _reroute_changes(
    edges: list[dict[str, Any]], prerequisite: str, substitute: str
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Build the change set that reroutes ``edges`` from ``prerequisite`` to ``substitute``.

    One ``remove_dependency`` plus one ``add_dependency`` per edge, in committed
    dependency order. The added edge preserves the original edge's
    ``dependency_type`` and ``constraint_parameters`` when present, so a soft or
    buffered edge is never silently upgraded by the reroute.
    """
    changes: list[dict[str, Any]] = []
    affected_ids: list[str] = []
    affected_dependencies: list[dict[str, Any]] = []
    for edge in edges:
        dependent = edge["dependent"]
        affected_ids.append(dependent)
        affected_dependencies.append(
            {"dependent": dependent, "prerequisite": prerequisite}
        )
        changes.append(
            {
                "action": REMOVE_DEPENDENCY,
                "dependent": dependent,
                "prerequisite": prerequisite,
            }
        )
        added: dict[str, Any] = {
            "action": ADD_DEPENDENCY,
            "dependent": dependent,
            "prerequisite": substitute,
        }
        if "dependency_type" in edge:
            added["dependency_type"] = edge["dependency_type"]
        if "constraint_parameters" in edge:
            added["constraint_parameters"] = copy.deepcopy(
                edge["constraint_parameters"]
            )
        changes.append(added)
    return changes, affected_ids, affected_dependencies


def _substitute_option(
    scenario: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    *,
    prerequisite: str,
    substitute: str,
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build and validate one substitute option (feasibility never guessed)."""
    changes, affected_ids, affected_dependencies = _reroute_changes(
        edges, prerequisite, substitute
    )

    structural: list[str] = []
    if substitute == prerequisite:
        structural.append(
            f"substitute id {substitute!r} equals the disrupted prerequisite; "
            f"replacing a prerequisite with itself is not a substitution."
        )
    if substitute not in by_id:
        structural.append(
            f"substitute id {substitute!r} does not name an existing entity in "
            f"this scenario; the declared candidate does not exist."
        )

    # Requirement: every candidate goes through the Milestone 2 gate, which
    # includes graph validation (cycles, unknown references, self-edges).
    gate = validate_hard_constraints(scenario, changes=changes)

    reasons = list(structural)
    if not gate["feasible"]:
        reasons.append(_GATE_FAILURE_REASON)
    feasible = gate["feasible"] and not structural

    explanation = (
        f"Substitute '{substitute}' for disrupted prerequisite '{prerequisite}': "
        f"reroute {len(edges)} dependent edge{'s' if len(edges) != 1 else ''} "
        f"({', '.join(affected_ids)}) from '{prerequisite}' to '{substitute}'."
    )
    limitations = [
        "Hypothetical only; the committed plan, dependencies, statuses, and "
        "version are not changed and nothing is committed.",
        "Feasibility covers hard-constraint and graph validity of the change "
        "set only; business readiness of the substitution is not assessable "
        "from the scenario and requires human review.",
    ]
    if not feasible:
        limitations.append(
            "Infeasible as reported: this option must not be adopted or "
            "recommended."
        )

    return {
        "option_id": _substitute_option_id(prerequisite, substitute),
        "explanation": explanation,
        "disrupted_entity_id": prerequisite,
        "substitute_entity_id": substitute,
        "affected_entity_ids": affected_ids,
        "affected_dependencies": affected_dependencies,
        "changes": changes,
        "feasible": feasible,
        "reasons": reasons,
        "violations": gate["violations"],
        "assumptions": [
            f"Assumes '{substitute}' can perform the role of '{prerequisite}' "
            f"for the listed dependents; the scenario declares the candidate "
            f"but carries no compatibility, availability, capacity, cost, or "
            f"risk data for it.",
            f"Assumes '{prerequisite}' is unavailable for this hypothetical "
            f"(single-disruption model); entity statuses are never changed.",
            "Assumes the substitution is exactly one remove_dependency plus "
            "one add_dependency per affected edge (preserving each edge's "
            "dependency_type and constraint_parameters); no unrelated "
            "dependency is modified.",
        ],
        "limitations": limitations,
    }


def find_substitute_options(
    scenario: dict[str, Any],
    *,
    entity_id: str,
) -> dict[str, Any]:
    """Propose substitute-service recovery options for a disrupted prerequisite.

    A candidate is proposed only when the disrupted entity explicitly declares
    ``metadata.substitutes`` -- a list of entity ids in the same scenario (the
    current schema's freeform entity metadata is the only place this data can
    live). Absent or empty declarations return
    ``outcome: "no_substitute_candidates"`` with ``missing_data`` explaining
    what is missing; the engine never infers candidates from names, statuses,
    resources, or graph structure and never invents a service, availability,
    compatibility, cost, or risk.

    Each declared candidate becomes one option
    (``substitute:{prerequisite}->{substitute}``) whose change set reroutes
    every dependency edge on ``entity_id`` -- one ``remove_dependency`` plus one
    ``add_dependency`` per dependent, preserving ``dependency_type`` and
    ``constraint_parameters`` -- validated with
    :func:`rippleback.constraints.validate_hard_constraints` (graph validation
    included). Options failing that gate, or structurally rejected (the
    candidate equals the disrupted entity, or names an unknown entity), are
    reported ``feasible: False`` with ``reasons`` and verbatim ``violations``.

    Outcomes: ``"substitutes_proposed"`` (at least one feasible option),
    ``"no_feasible_substitute"`` (candidates declared, none feasible), or
    ``"no_substitute_candidates"`` (no candidate data; see ``missing_data``).

    The committed ``scenario`` is never modified; option order follows
    declaration order (duplicates removed), so identical input yields
    identical output. The resulting ``options`` carry a standard ``changes``
    list and unique ``option_id``s, so they can be passed to
    :func:`rippleback.forecast.recalculate_delivery_forecast`,
    :func:`rippleback.scoring.rank_options`, or
    :func:`rippleback.comparison.compare_recovery_options` unchanged.

    Raises:
        RecoveryError: unknown ``entity_id``, or a malformed
            ``metadata.substitutes`` declaration (not a list of strings).
        ConstraintError: malformed scenario (Milestone 2 contract, propagated
            from the baseline validation performed before any proposal).
    """
    # Baseline validation first: malformed scenarios raise ConstraintError
    # (never KeyError), matching the Milestone 2/3 engines.
    validate_hard_constraints(scenario, changes=[])

    entities: list[dict[str, Any]] = scenario["entities"]
    by_id = {entity["id"]: entity for entity in entities}
    if entity_id not in by_id:
        raise RecoveryError(
            f"unknown entity_id {entity_id!r}; available: {sorted(by_id)}"
        )

    edges = [
        dependency
        for dependency in scenario["dependencies"]
        if dependency["prerequisite"] == entity_id
    ]
    declared = _declared_substitutes(by_id[entity_id])
    candidates = declared or []

    options = (
        [
            _substitute_option(
                scenario,
                by_id,
                prerequisite=entity_id,
                substitute=substitute,
                edges=edges,
            )
            for substitute in candidates
        ]
        if edges and candidates
        else []
    )

    missing_data: list[str] = []
    if not options:
        if declared is None:
            missing_data.append(
                f"entity {entity_id!r} declares no "
                f"'metadata.{SUBSTITUTES_METADATA_KEY}' list, and no other "
                f"field of this scenario identifies a service that could "
                f"substitute for it; add an explicit list of substitute "
                f"entity ids to propose candidates."
            )
        elif not candidates:
            missing_data.append(
                f"entity {entity_id!r} declares an empty "
                f"'metadata.{SUBSTITUTES_METADATA_KEY}' list, so no "
                f"substitute service is identified."
            )
        if not edges:
            missing_data.append(
                f"no dependency edge has {entity_id!r} as its prerequisite, "
                f"so there is no dependency to reroute to a substitute."
            )

    if any(option["feasible"] for option in options):
        outcome = OUTCOME_SUBSTITUTES_PROPOSED
    elif options:
        outcome = OUTCOME_NO_FEASIBLE_SUBSTITUTE
    else:
        outcome = OUTCOME_NO_SUBSTITUTE_CANDIDATES

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "disruption": {"entity_id": entity_id, "name": by_id[entity_id]["name"]},
        "outcome": outcome,
        "options": options,
        "missing_data": missing_data,
        "assumptions": [
            "A substitute candidate exists only when the disrupted "
            "prerequisite explicitly declares 'metadata.substitutes' -- a "
            "list of entity ids in the same scenario (entity metadata is "
            "freeform in the current schema); absent or empty declarations "
            "yield no candidates and nothing is inferred from names, "
            "statuses, resources, or graph structure.",
            "Candidates must name existing entities in the same scenario; the "
            "engine invents no service, availability, compatibility, cost, or "
            "risk for them.",
            "Every option reroutes exactly the disrupted prerequisite's "
            "dependency edges -- one remove_dependency plus one add_dependency "
            "per dependent, preserving each edge's dependency_type and "
            "constraint_parameters -- and is validated with "
            "rippleback.constraints.validate_hard_constraints (which includes "
            "graph validation); an option is feasible only when that gate "
            "passes and the substitution itself is well-formed.",
            "Options follow metadata declaration order with duplicates "
            "removed (first occurrence kept); their change sets follow "
            "committed dependency order, so identical input yields identical "
            "output.",
            "Single-disruption model: the disrupted entity is treated as the "
            "only unavailable entity; the committed plan, statuses, and "
            "version are never modified.",
        ],
        "limitations": [
            "Hypothetical only: the committed plan, dependencies, statuses, "
            "and version are unchanged, and no recovery is committed or "
            "approved here.",
            "Feasibility covers hard-constraint and graph validity only; "
            "business readiness (compatibility, availability, capacity, cost, "
            "risk) is not assessable from this schema and must be reviewed "
            "by a human.",
            "Options are not ranked here; pass an option's 'changes' to "
            "rippleback.forecast.recalculate_delivery_forecast or the whole "
            "option to rippleback.scoring.rank_options / "
            "rippleback.comparison.compare_recovery_options for delay "
            "scoring, gating, and ranking.",
            "'metadata.substitutes' is an engine-level convention read from "
            "freeform entity metadata: docs/data-contract.md does not yet "
            "recognize the key and no shipped fixture declares it, so shipped "
            "scenarios report no candidates.",
        ],
    }
