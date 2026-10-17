"""Longest-path critical path over the hard dependency graph.

Phase 6, first slice. Given a scenario, :func:`critical_path` returns the
longest chain of **hard** dependencies (``requires_completion`` /
``requires_arrival_by``). When entities declare a positive ``duration`` the
chain is weighted by duration; otherwise every entity counts as one unit, so the
critical path is the longest chain by number of steps.

Soft (``benefits_from``) edges never define ordering and are ignored here.

This module is **pure**: it never mutates the committed scenario and returns
identical output for identical input. Ties are broken lexicographically so the
result is stable.

Honesty note: the engine has **no wall-clock dates** and, unless fixtures declare
durations, no time dimension at all. The reported ``length`` is a relative
ranking of dependency chains, not a delivery date or duration forecast.
"""

from __future__ import annotations

from typing import Any

from rippleback.graph import build_graph, entity_duration, is_external


def _hard_adjacency(scenario: dict[str, Any]) -> tuple[list[str], dict[str, list[str]], dict[str, float]]:
    """Return (entity order, hard adjacency, duration by id) for a scenario."""
    graph = build_graph(scenario)  # raises GraphError on cycles/bad refs
    order = [entity["id"] for entity in scenario["entities"]]
    adjacency: dict[str, list[str]] = {entity_id: [] for entity_id in order}
    for dependency in scenario["dependencies"]:
        prerequisite = dependency["prerequisite"]
        dependent = dependency["dependent"]
        if graph.edges[prerequisite, dependent]["hard"]:
            adjacency[prerequisite].append(dependent)
    for node in adjacency:
        adjacency[node] = sorted(adjacency[node])
    durations = {entity_id: 0.0 for entity_id in order}
    return order, adjacency, durations


def _duration_by_id(scenario: dict[str, Any]) -> dict[str, float]:
    return {
        entity["id"]: entity_duration(entity) for entity in scenario["entities"]
    }


def _longest_path(
    nodes: list[str],
    adjacency: dict[str, list[str]],
    duration: dict[str, float],
) -> tuple[list[str], float]:
    """Longest weighted path restricted to ``nodes`` (deterministic).

    Nodes must already be acyclic under ``adjacency``. Returns ``(path, length)``
    where ``length`` is the sum of node durations along the path. Ties break on
    the lexicographically smallest node id at every step, so the result is stable.
    """
    allowed = set(nodes)
    indegree = {node: 0 for node in nodes}
    for node in nodes:
        for successor in adjacency.get(node, []):
            if successor in allowed:
                indegree[successor] += 1

    ready = sorted(node for node in nodes if indegree[node] == 0)
    best: dict[str, tuple[float, str | None]] = {
        node: (duration.get(node, 1.0), None) for node in nodes
    }

    processed: list[str] = []
    while ready:
        current = ready.pop(0)
        processed.append(current)
        for successor in sorted(adjacency.get(current, [])):
            if successor not in allowed:
                continue
            candidate = best[current][0] + duration.get(successor, 1.0)
            if candidate > best[successor][0]:
                best[successor] = (candidate, current)
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()

    if len(processed) != len(nodes):
        # Defensive: a cycle would have been rejected earlier by build_graph.
        return [], 0.0

    end = None
    for node in nodes:
        if end is None or best[node][0] > best[end][0]:
            end = node
        elif best[node][0] == best[end][0] and node < end:
            end = node
    if end is None:
        return [], 0.0

    path: list[str] = []
    cursor: str | None = end
    while cursor is not None:
        path.append(cursor)
        cursor = best[cursor][1]
    path.reverse()
    return path, best[end][0]


def _path_edges(scenario: dict[str, Any], path: list[str]) -> list[dict[str, str]]:
    """Return the dependency dicts along ``path`` (consecutive pairs)."""
    index = {
        (dependency["prerequisite"], dependency["dependent"]): dependency
        for dependency in scenario["dependencies"]
    }
    edges: list[dict[str, str]] = []
    for source, target in zip(path, path[1:]):
        dependency = index.get((source, target), {})
        edges.append(
            {
                "dependent": target,
                "prerequisite": source,
                "dependency_type": dependency.get(
                    "dependency_type", "requires_completion"
                ),
            }
        )
    return edges


def critical_path(scenario: dict[str, Any]) -> dict[str, Any]:
    """Return the longest hard dependency chain for a scenario.

    The result is deterministic and descriptive:

    * ``path`` -- entity ids along the critical path, prerequisite first.
    * ``length`` -- sum of node durations (or steps when durations are absent).
    * ``unit`` -- ``"duration_units"`` when any entity declares a duration, else
      ``"steps"``.
    * ``edges`` -- the dependency dicts joining the path, for evidence.
    """
    order, adjacency, _ = _hard_adjacency(scenario)
    duration = _duration_by_id(scenario)
    path, length = _longest_path(order, adjacency, duration)
    explicit = any("duration" in entity for entity in scenario["entities"])
    return {
        "path": path,
        "length": length,
        "unit": "duration_units" if explicit else "steps",
        "edges": _path_edges(scenario, path),
        "assumptions": [
            "Critical path follows hard requires edges only; benefits_from "
            "edges never define ordering.",
        ],
        "limitations": [
            "No wall-clock dates are modeled; length is a relative ranking of "
            "dependency chains, not a delivery date or forecast.",
        ],
    }


def _hard_reachable(scenario: dict[str, Any], entity_id: str) -> list[str]:
    """Entity ids reachable from ``entity_id`` over hard edges, in entity order."""
    _, adjacency, _ = _hard_adjacency(scenario)
    seen: set[str] = set()
    stack = [entity_id]
    while stack:
        current = stack.pop()
        for successor in adjacency.get(current, []):
            if successor not in seen:
                seen.add(successor)
                stack.append(successor)
    order = [entity["id"] for entity in scenario["entities"]]
    return [node for node in order if node in seen]


def critical_path_changes(scenario: dict[str, Any], entity_id: str) -> dict[str, Any]:
    """Report the critical path before and within the disruption's impact set.

    ``entity_id`` must exist in the scenario; :class:`~rippleback.graph.GraphError`
    is raised otherwise (via :func:`critical_path`). The *disrupted* path is the
    longest hard chain within the entities the disruption hard-affects (plus the
    disrupted entity itself).
    """
    baseline = critical_path(scenario)
    impacted_nodes = [entity_id, *_hard_reachable(scenario, entity_id)]
    order, adjacency, _ = _hard_adjacency(scenario)
    duration = _duration_by_id(scenario)
    disrupted_path, disrupted_length = _longest_path(
        impacted_nodes, adjacency, duration
    )
    baseline_set = set(baseline["path"])
    impacted_set = set(impacted_nodes)
    external_root = any(
        entity["id"] == entity_id and is_external(entity)
        for entity in scenario["entities"]
    )
    return {
        "baseline_path": baseline["path"],
        "baseline_length": baseline["length"],
        "unit": baseline["unit"],
        "disrupted_entity_id": entity_id,
        "impacted_entity_ids": impacted_nodes,
        "disrupted_path": disrupted_path,
        "disrupted_length": disrupted_length,
        "baseline_intersects_disruption": bool(baseline_set & impacted_set),
        "disrupted_entity_is_external": external_root,
        "assumptions": [
            "The disruption affects exactly the entities reachable over hard "
            "requires edges (single-disruption model).",
        ],
        "limitations": [
            "Critical-path change is structural: it does not compute a new "
            "delivery date or the size of any delay.",
        ],
    }
