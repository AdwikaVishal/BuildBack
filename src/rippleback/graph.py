"""Directed, typed dependency graph construction and validation.

Phase 6, first slice. The dependency engine models a plan as a directed, typed
graph ``G = (V, E)`` using NetworkX. This module is **pure**: it never mutates
the committed scenario. It builds a fresh graph every call.

Edge direction
--------------
An edge points **from the prerequisite to the dependent**, i.e. the direction
in which unavailability propagates:

    payment-api  ──requires_completion──▶  backend-integration

Read it as "unavailability of ``payment-api`` can affect
``backend-integration``" and, equivalently, "``backend-integration`` is
completed only after ``payment-api``". This is the same direction used by
:mod:`rippleback.impact`.

Edge types
----------
Each edge carries an explicit ``dependency_type``:

* ``requires_completion`` -- a **hard** edge: the dependent cannot be completed
  until the prerequisite is completed.
* ``requires_arrival_by`` -- a **hard** time edge: the dependent must start (or
  arrive) after the prerequisite, subject to
  ``constraint_parameters`` such as a required buffer.
* ``benefits_from`` -- a **soft** edge: the dependent does not require the
  prerequisite but is better off with it. A soft edge only adds *risk*, never a
  block.

Dependencies without a ``dependency_type`` default to ``requires_completion`` so
older fixtures keep working.

Validation and the error contract
---------------------------------
:func:`build_graph` raises :class:`GraphError` with a stable ``code`` for every
structural fault, matching the RippleBack error contract:

* ``ENTITY_NOT_FOUND`` -- an edge references an entity that does not exist.
* ``INVALID_DEPENDENCY`` -- a malformed or self-referential edge.
* ``INVALID_DEPENDENCY_TYPE`` -- an unknown ``dependency_type``.
* ``CYCLIC_DEPENDENCY`` -- the dependency graph is not acyclic.

:func:`graph_errors` returns the same faults as coded dicts instead of raising,
for callers that want to report rather than abort.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

EDGE_REQUIRES_COMPLETION = "requires_completion"
EDGE_REQUIRES_ARRIVAL_BY = "requires_arrival_by"
EDGE_BENEFITS_FROM = "benefits_from"

HARD_EDGE_TYPES = frozenset({EDGE_REQUIRES_COMPLETION, EDGE_REQUIRES_ARRIVAL_BY})
SOFT_EDGE_TYPES = frozenset({EDGE_BENEFITS_FROM})
KNOWN_EDGE_TYPES = HARD_EDGE_TYPES | SOFT_EDGE_TYPES
DEFAULT_EDGE_TYPE = EDGE_REQUIRES_COMPLETION

ERROR_ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ERROR_INVALID_DEPENDENCY = "INVALID_DEPENDENCY"
ERROR_INVALID_DEPENDENCY_TYPE = "INVALID_DEPENDENCY_TYPE"
ERROR_CYCLIC_DEPENDENCY = "CYCLIC_DEPENDENCY"

# Attribute keys used on graph nodes/edges, kept as stable string constants so
# consumers never depend on magic literals.
NODE_NAME = "name"
NODE_STATUS = "status"
NODE_DURATION = "duration"
NODE_EXTERNAL = "external"
NODE_SLACK_MINUTES = "slack_minutes"
NODE_METADATA = "metadata"
EDGE_TYPE = "dependency_type"
EDGE_HARD = "hard"
EDGE_CONSTRAINT_PARAMETERS = "constraint_parameters"


class GraphError(ValueError):
    """Raised for structural dependency-graph faults.

    Carries a stable :attr:`code` from the RippleBack error contract plus a
    human-readable :attr:`message`.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def edge_type(dependency: dict[str, Any]) -> str:
    """Return the declared ``dependency_type``, defaulting for older fixtures."""
    value = dependency.get("dependency_type", DEFAULT_EDGE_TYPE)
    return value if isinstance(value, str) else ""


def is_hard(dependency_type: str) -> bool:
    """True when the edge blocks its dependent (not merely adds risk)."""
    return dependency_type in HARD_EDGE_TYPES


def entity_metadata(entity: dict[str, Any]) -> dict[str, Any]:
    """Return an entity's metadata dict, or an empty dict when absent/untyped."""
    metadata = entity.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def is_external(entity: dict[str, Any]) -> bool:
    """True when the entity's owning party is outside the team.

    Marked in fixture data as ``metadata: {"external": true}`` (see the block
    triage ``external`` state). Only a real JSON/Python ``true`` counts.
    """
    return entity_metadata(entity).get("external") is True


def entity_slack_minutes(entity: dict[str, Any]) -> float | None:
    """Return the entity's slack in minutes, or ``None`` when unspecified."""
    value = entity_metadata(entity).get("slack_minutes")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def entity_duration(entity: dict[str, Any]) -> float:
    """Return the entity's duration, defaulting to one unit when unspecified."""
    value = entity.get("duration")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return 1.0
    return float(value)


def detect_cycle(edges: list[tuple[str, str]]) -> list[str] | None:
    """Return one dependency cycle as an ordered entity list, or ``None``.

    ``edges`` are ``(prerequisite, dependent)`` pairs. Only the first cycle
    found is returned; ``networkx`` raises ``NetworkXNoCycle`` when the graph is
    acyclic. The returned list repeats the first entity at the end to show the
    loop, e.g. ``["a", "b", "c", "a"]``.
    """
    graph = nx.DiGraph()
    graph.add_edges_from(edges)
    if nx.is_directed_acyclic_graph(graph):
        return None
    cycle_nodes = nx.find_cycle(graph)
    ordered = [source for source, _ in cycle_nodes]
    ordered.append(ordered[0])
    return ordered


def graph_errors(scenario: dict[str, Any]) -> list[dict[str, str]]:
    """Return all structural faults as ``{code, message}`` dicts (never raises).

    Ordering follows fixture dependency order, then a single trailing cycle
    fault, so repeated calls are identical.
    """
    errors: list[dict[str, str]] = []
    entity_ids = {
        entity["id"]
        for entity in scenario.get("entities", [])
        if isinstance(entity, dict) and isinstance(entity.get("id"), str)
    }

    valid_edges: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, dependency in enumerate(scenario.get("dependencies", [])):
        if not isinstance(dependency, dict):
            errors.append(
                {
                    "code": ERROR_INVALID_DEPENDENCY,
                    "message": f"dependencies[{index}] must be a dict",
                }
            )
            continue
        prerequisite = dependency.get("prerequisite")
        dependent = dependency.get("dependent")
        if not isinstance(prerequisite, str) or not isinstance(dependent, str):
            errors.append(
                {
                    "code": ERROR_INVALID_DEPENDENCY,
                    "message": f"dependencies[{index}] endpoints must be strings",
                }
            )
            continue
        missing = [ref for ref in (prerequisite, dependent) if ref not in entity_ids]
        if missing:
            errors.append(
                {
                    "code": ERROR_ENTITY_NOT_FOUND,
                    "message": (
                        f"dependencies[{index}] references unknown entity id(s): "
                        f"{sorted(missing)}"
                    ),
                }
            )
            continue
        if prerequisite == dependent:
            errors.append(
                {
                    "code": ERROR_INVALID_DEPENDENCY,
                    "message": (
                        f"dependencies[{index}] is a self-dependency on "
                        f"{prerequisite!r}"
                    ),
                }
            )
            continue
        declared = edge_type(dependency)
        if declared not in KNOWN_EDGE_TYPES:
            errors.append(
                {
                    "code": ERROR_INVALID_DEPENDENCY_TYPE,
                    "message": (
                        f"dependencies[{index}] has unknown dependency_type "
                        f"{declared!r}; known: {sorted(KNOWN_EDGE_TYPES)}"
                    ),
                }
            )
            continue
        pair = (prerequisite, dependent)
        if pair in seen_pairs:
            errors.append(
                {
                    "code": ERROR_INVALID_DEPENDENCY,
                    "message": (
                        f"dependencies[{index}] duplicates the edge "
                        f"{dependent!r} requires {prerequisite!r}"
                    ),
                }
            )
            continue
        seen_pairs.add(pair)
        valid_edges.append(pair)

    cycle = detect_cycle(valid_edges)
    if cycle is not None:
        errors.append(
            {
                "code": ERROR_CYCLIC_DEPENDENCY,
                "message": f"dependency cycle detected: {' -> '.join(cycle)}",
            }
        )
    return errors


def build_graph(scenario: dict[str, Any]) -> nx.DiGraph:
    """Build and validate the directed, typed dependency graph for a scenario.

    Nodes carry ``name``, ``status``, ``duration``, ``external``,
    ``slack_minutes``, and ``metadata``. Edges run prerequisite -> dependent and
    carry ``dependency_type``, ``hard``, and ``constraint_parameters``.

    Raises:
        GraphError: with a stable ``code`` when the graph is structurally
            invalid (unknown reference, self/duplicate edge, unknown edge type,
            or a cycle).
    """
    errors = graph_errors(scenario)
    if errors:
        first = errors[0]
        raise GraphError(first["code"], first["message"])

    graph = nx.DiGraph()
    for entity in scenario["entities"]:
        graph.add_node(
            entity["id"],
            name=entity.get("name"),
            status=entity.get("status"),
            duration=entity_duration(entity),
            external=is_external(entity),
            slack_minutes=entity_slack_minutes(entity),
            metadata=entity_metadata(entity),
        )
    for dependency in scenario["dependencies"]:
        prerequisite = dependency["prerequisite"]
        dependent = dependency["dependent"]
        declared = edge_type(dependency)
        parameters = dependency.get("constraint_parameters")
        graph.add_edge(
            prerequisite,
            dependent,
            dependency_type=declared,
            hard=declared in HARD_EDGE_TYPES,
            constraint_parameters=(
                dict(parameters) if isinstance(parameters, dict) else {}
            ),
        )
    return graph


def hard_successors(graph: nx.DiGraph, node: str) -> list[str]:
    """Deterministically ordered hard dependents of ``node``."""
    return sorted(
        successor
        for successor in graph.successors(node)
        if graph.edges[node, successor][EDGE_HARD]
    )


def soft_successors(graph: nx.DiGraph, node: str) -> list[str]:
    """Deterministically ordered soft (``benefits_from``) dependents of ``node``."""
    return sorted(
        successor
        for successor in graph.successors(node)
        if not graph.edges[node, successor][EDGE_HARD]
    )
