"""Blocker triage: separate genuine blockers from work that can still proceed.

Phase 6, the BuildBack headline feature. After a disruption, every entity gets
**exactly one** state so the team stops only what must stop:

============  ================================================================
State         Rule
============  ================================================================
``done``      The entity is already completed (``status == "done"``). Finished
              work is never re-labelled as blocked.
``blocked``   A hard ``requires_*`` path leads back to the disrupted entity and
              the dependent has no slack to absorb the disruption.
``external``  Like ``blocked``, but the blocked party is outside the team (an
              entity on the evidence path is marked ``metadata.external``), so
              the team cannot fix it directly.
``at_risk``   Not blocked, but a soft ``benefits_from`` edge links it to the
              disruption, or it has little slack left.
``healthy``   No hard or soft path from the disruption.
============  ================================================================

Precedence for the single state is ``done`` > ``blocked``/``external`` >
``at_risk`` > ``healthy``.

Time-aware blockage
-------------------
``service_outage`` and ``resource_unavailable`` are unbounded: every entity on a
hard path is blocked. ``delivery_delay`` and ``transport_delay`` carry a
``delay_minutes``; a dependent absorbs the delay while its
``metadata.slack_minutes`` lasts. A dependent is blocked only while the arriving
delay exceeds its slack, so a downstream event is **not** assumed to fail when a
buffer absorbs the delay (the travel case). Delay is propagated in topological
order and the worst incoming delay wins.

The engine is **pure and deterministic**: the committed scenario is never
modified and identical input yields identical output.

The recovery-view rule is implemented by :func:`recompute_with_changes`: apply a
change set (for example remove the broken edge so a mock service satisfies the
``requires`` edge) and recompute every state to show the blocked -> healthy
payoff.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from rippleback.critical_path import critical_path, critical_path_changes
from rippleback.graph import (
    EDGE_BENEFITS_FROM,
    build_graph,
    entity_slack_minutes,
    is_external,
)

STATE_DONE = "done"
STATE_BLOCKED = "blocked"
STATE_EXTERNAL = "external"
STATE_AT_RISK = "at_risk"
STATE_HEALTHY = "healthy"

KNOWN_STATES = (
    STATE_DONE,
    STATE_BLOCKED,
    STATE_EXTERNAL,
    STATE_AT_RISK,
    STATE_HEALTHY,
)

EVENT_SERVICE_OUTAGE = "service_outage"
EVENT_RESOURCE_UNAVAILABLE = "resource_unavailable"
EVENT_DELIVERY_DELAY = "delivery_delay"
EVENT_TRANSPORT_DELAY = "transport_delay"

VALID_EVENT_TYPES = (
    EVENT_SERVICE_OUTAGE,
    EVENT_RESOURCE_UNAVAILABLE,
    EVENT_DELIVERY_DELAY,
    EVENT_TRANSPORT_DELAY,
)

UNBOUNDED_EVENT_TYPES = frozenset({EVENT_SERVICE_OUTAGE, EVENT_RESOURCE_UNAVAILABLE})
DELAY_EVENT_TYPES = frozenset({EVENT_DELIVERY_DELAY, EVENT_TRANSPORT_DELAY})

DEFAULT_EVENT_TYPE = EVENT_SERVICE_OUTAGE

#: An entity with declared slack at or below this many minutes is ``at_risk``
#: even when no dependency path reaches it. Chosen for the seeded fixtures;
#: flagged as an open question in ``docs/data-contract.md``.
AT_RISK_SLACK_MINUTES = 60.0

ERROR_ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ERROR_INVALID_DISRUPTION = "INVALID_DISRUPTION"


class TriageError(ValueError):
    """Raised for invalid triage requests (unknown entity, bad event)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _hard_adjacency(scenario: dict[str, Any]) -> dict[str, list[str]]:
    """prerequisite -> sorted hard dependents, in fixture order for ties."""
    adjacency: dict[str, list[str]] = {e["id"]: [] for e in scenario["entities"]}
    for dependency in scenario["dependencies"]:
        declared = dependency.get("dependency_type", "requires_completion")
        # benefits_from is the only soft edge; everything else is hard.
        if declared != EDGE_BENEFITS_FROM:
            adjacency[dependency["prerequisite"]].append(dependency["dependent"])
    for node in adjacency:
        adjacency[node] = sorted(adjacency[node])
    return adjacency


def _soft_adjacency(scenario: dict[str, Any]) -> dict[str, list[str]]:
    """prerequisite -> sorted soft (``benefits_from``) dependents."""
    adjacency: dict[str, list[str]] = {e["id"]: [] for e in scenario["entities"]}
    for dependency in scenario["dependencies"]:
        if dependency.get("dependency_type", "requires_completion") == EDGE_BENEFITS_FROM:
            adjacency[dependency["prerequisite"]].append(dependency["dependent"])
    for node in adjacency:
        adjacency[node] = sorted(adjacency[node])
    return adjacency


def _shortest_paths(
    target: str, adjacency: dict[str, list[str]]
) -> dict[str, list[str]]:
    """BFS shortest path (list of entity ids) from ``target`` to each node."""
    paths: dict[str, list[str]] = {target: [target]}
    queue: deque[str] = deque([target])
    while queue:
        current = queue.popleft()
        for successor in adjacency.get(current, []):
            if successor not in paths:
                paths[successor] = [*paths[current], successor]
                queue.append(successor)
    return paths


def _topological_order(nodes: list[str], adjacency: dict[str, list[str]]) -> list[str]:
    """Deterministic Kahn topological order restricted to ``nodes``."""
    allowed = set(nodes)
    indegree = {node: 0 for node in nodes}
    for node in nodes:
        for successor in adjacency.get(node, []):
            if successor in allowed:
                indegree[successor] += 1
    ready = sorted(node for node in nodes if indegree[node] == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for successor in sorted(adjacency.get(current, [])):
            if successor not in allowed:
                continue
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()
    return order


def _edge_lookup(scenario: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (dependency["prerequisite"], dependency["dependent"]): dependency
        for dependency in scenario["dependencies"]
    }


def _evidence_edges(
    scenario: dict[str, Any], path: list[str]
) -> list[dict[str, Any]]:
    lookup = _edge_lookup(scenario)
    edges: list[dict[str, Any]] = []
    for source, target in zip(path, path[1:]):
        dependency = lookup.get((source, target), {})
        edges.append(
            {
                "dependent": target,
                "prerequisite": source,
                "dependency_type": dependency.get("dependency_type", "requires_completion"),
            }
        )
    return edges


def _delay_minutes(
    event_type: str, parameters: dict[str, Any] | None
) -> float | None:
    """Validate and return the disruption delay, or ``None`` when unbounded."""
    if event_type in UNBOUNDED_EVENT_TYPES:
        return None
    if event_type in DELAY_EVENT_TYPES:
        parameters = parameters or {}
        value = parameters.get("delay_minutes")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise TriageError(
                ERROR_INVALID_DISRUPTION,
                f"event_type {event_type!r} requires a non-negative numeric "
                f"'delay_minutes' in parameters",
            )
        return float(value)
    raise TriageError(
        ERROR_INVALID_DISRUPTION,
        f"unknown event_type {event_type!r}; allowed: {list(VALID_EVENT_TYPES)}",
    )


def _entity_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entity["id"]: entity for entity in scenario["entities"]}


def analyze_blocked_tasks(
    scenario: dict[str, Any],
    *,
    entity_id: str,
    event_type: str = DEFAULT_EVENT_TYPE,
    reason: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one triage state per entity plus the work that can still proceed.

    The committed ``scenario`` is never modified. Unknown entities raise
    :class:`TriageError` ``ENTITY_NOT_FOUND``; unknown events and missing delay
    parameters raise ``INVALID_DISRUPTION``. A cyclic or malformed graph raises
    :class:`~rippleback.graph.GraphError`.
    """
    if event_type not in VALID_EVENT_TYPES:
        raise TriageError(
            ERROR_INVALID_DISRUPTION,
            f"unknown event_type {event_type!r}; allowed: {list(VALID_EVENT_TYPES)}",
        )
    delay = _delay_minutes(event_type, parameters)
    reason_text = reason.strip() if isinstance(reason, str) and reason.strip() else None

    entities = scenario["entities"]
    by_id = _entity_map(scenario)
    if entity_id not in by_id:
        raise TriageError(
            ERROR_ENTITY_NOT_FOUND,
            f"unknown entity_id {entity_id!r}; available: {sorted(by_id)}",
        )

    # build_graph validates references/cycles and raises GraphError with a code.
    build_graph(scenario)

    hard = _hard_adjacency(scenario)
    soft = _soft_adjacency(scenario)
    hard_paths = _shortest_paths(entity_id, hard)

    # Soft risk: nodes reachable from the disruption (or any hard-affected node)
    # over benefits_from edges. Keep the shortest evidence path to each node.
    soft_paths: dict[str, list[str]] = {}
    for source in [entity_id, *sorted(hard_paths)]:
        for node, path in _shortest_paths(source, soft).items():
            if node == source:
                continue
            if node not in soft_paths or len(path) < len(soft_paths[node]):
                soft_paths[node] = path
    for node in hard_paths:
        soft_paths.pop(node, None)

    # Delay propagation (time-aware) over hard edges.
    remaining: dict[str, float] = {entity_id: float("inf")}
    blocked_nodes: set[str] = set()
    feasible_nodes: dict[str, float] = {}
    if delay is None:
        remaining[entity_id] = float("inf")
        for node in hard_paths:
            if node != entity_id:
                blocked_nodes.add(node)
    else:
        remaining[entity_id] = delay
        order = _topological_order(list(hard_paths), hard)
        for node in order:
            if node == entity_id:
                continue
            predecessors = [
                source
                for source in hard_paths
                if node in hard.get(source, [])
            ]
            incoming = max(
                (remaining.get(source, 0.0) for source in predecessors),
                default=0.0,
            )
            slack = entity_slack_minutes(by_id[node]) or 0.0
            if incoming <= slack:
                remaining[node] = 0.0
                if incoming > 0.0:
                    feasible_nodes[node] = incoming
            else:
                remaining[node] = incoming - slack
                blocked_nodes.add(node)

    external_ids = {eid for eid in by_id if is_external(by_id[eid])}

    states: list[dict[str, Any]] = []
    for entity in entities:
        eid = entity["id"]
        if eid == entity_id:
            state = STATE_EXTERNAL if eid in external_ids else STATE_BLOCKED
            paths = hard_paths
            path = [entity_id]
            why = (
                f"'{eid}' is the disrupted entity; the "
                f"{event_type.replace('_', ' ')} marks it unavailable."
            )
        elif entity.get("status") == "done":
            state = STATE_DONE
            path = []
            why = f"'{eid}' is already completed, so the disruption does not re-block it."
        elif eid in blocked_nodes:
            path = hard_paths.get(eid, [eid])
            external_blocker = any(node in external_ids for node in path)
            state = STATE_EXTERNAL if external_blocker else STATE_BLOCKED
            why = (
                f"Hard dependency path from '{entity_id}' reaches '{eid}'"
                + (
                    "; the blocking party is outside the team."
                    if external_blocker
                    else " and no alternative satisfies it."
                )
            )
        elif eid in feasible_nodes:
            path = hard_paths.get(eid, [eid])
            state = STATE_AT_RISK
            why = (
                f"'{eid}' is delayed by the disruption but its slack absorbs "
                f"the delay, so it is not blocked."
            )
        elif eid in soft_paths:
            path = soft_paths[eid]
            state = STATE_AT_RISK
            why = (
                f"'{eid}' benefits_from '{path[-2] if len(path) > 1 else entity_id}' "
                f"but does not require it, so the disruption only adds risk."
            )
        else:
            slack = entity_slack_minutes(entity)
            if slack is not None and slack <= AT_RISK_SLACK_MINUTES:
                path = [eid]
                state = STATE_AT_RISK
                why = (
                    f"'{eid}' has only {slack:g} minute(s) of slack, at or below "
                    f"the {AT_RISK_SLACK_MINUTES:g}-minute at-risk threshold."
                )
            else:
                path = []
                state = STATE_HEALTHY
                why = f"No dependency path links '{eid}' to the disruption."

        states.append(
            {
                "entity_id": eid,
                "name": entity.get("name"),
                "state": state,
                "reason": why,
                "evidence_path": path,
                "evidence_edges": _evidence_edges(scenario, path),
            }
        )

    summary = {state: 0 for state in KNOWN_STATES}
    for entry in states:
        summary[entry["state"]] += 1

    parallel_work = [
        {"entity_id": entry["entity_id"], "name": entry["name"]}
        for entry in states
        if entry["state"] in {STATE_HEALTHY, STATE_AT_RISK}
    ]

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "disruption": {
            "entity_id": entity_id,
            "name": by_id[entity_id].get("name"),
            "event_type": event_type,
            "reason": reason_text,
            "delay_minutes": delay,
        },
        "states": states,
        "summary": summary,
        "parallel_work": parallel_work,
        "critical_path": critical_path(scenario),
        "critical_path_change": critical_path_changes(scenario, entity_id),
        "assumptions": [
            "Every entity is classified from the committed dependency graph for "
            "one disruption at a time.",
            "benefits_from edges add risk but never block.",
            "Hard reachability marks blockage; a 'done' entity is reported done "
            "and does not re-open.",
        ],
        "limitations": [
            "The disrupted entity is classified blocked (or external) by this "
            "engine; its committed status is overridden by the event.",
            f"Low-slack at-risk uses a fixed {AT_RISK_SLACK_MINUTES:g}-minute "
            "threshold (open question).",
            "No recovery option is applied here; use recompute_with_changes to "
            "show the blocked-to-healthy payoff.",
        ],
    }


def apply_changes(
    scenario: dict[str, Any], changes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return a new dependency list with ``changes`` applied (committed untouched).

    Each change is ``{"action": "remove_dependency" | "add_dependency",
    "dependent": ..., "prerequisite": ..., optional "dependency_type": ...}``.
    Unknown actions or malformed changes raise
    :class:`TriageError` ``INVALID_DISRUPTION``.
    """
    dependencies = [dict(dependency) for dependency in scenario["dependencies"]]
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise TriageError(
                ERROR_INVALID_DISRUPTION, f"changes[{index}] must be a dict"
            )
        action = change.get("action")
        dependent = change.get("dependent")
        prerequisite = change.get("prerequisite")
        if not isinstance(dependent, str) or not isinstance(prerequisite, str):
            raise TriageError(
                ERROR_INVALID_DISRUPTION,
                f"changes[{index}] requires string 'dependent' and 'prerequisite'",
            )
        if action == "remove_dependency":
            dependencies = [
                dependency
                for dependency in dependencies
                if not (
                    dependency["dependent"] == dependent
                    and dependency["prerequisite"] == prerequisite
                )
            ]
        elif action == "add_dependency":
            entry: dict[str, Any] = {
                "dependent": dependent,
                "prerequisite": prerequisite,
            }
            declared = change.get("dependency_type")
            if declared is not None:
                entry["dependency_type"] = declared
            parameters = change.get("constraint_parameters")
            if isinstance(parameters, dict):
                entry["constraint_parameters"] = dict(parameters)
            if not any(
                dependency["dependent"] == dependent
                and dependency["prerequisite"] == prerequisite
                for dependency in dependencies
            ):
                dependencies.append(entry)
        else:
            raise TriageError(
                ERROR_INVALID_DISRUPTION,
                f"changes[{index}] has unknown action {action!r}; allowed: "
                "['add_dependency', 'remove_dependency']",
            )
    return dependencies


def recompute_with_changes(
    scenario: dict[str, Any],
    *,
    entity_id: str,
    changes: list[dict[str, Any]],
    event_type: str = DEFAULT_EVENT_TYPE,
    reason: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-run triage on a modified plan and report before/after state changes.

    This implements the recovery-view rule: an entity moves from ``blocked`` to
    ``healthy`` when a change set (for example a mock service that satisfies the
    ``requires`` edge) removes the dependency path. The committed scenario is
    never modified.
    """
    before = analyze_blocked_tasks(
        scenario,
        entity_id=entity_id,
        event_type=event_type,
        reason=reason,
        parameters=parameters,
    )
    modified = {**scenario, "dependencies": apply_changes(scenario, changes)}
    after = analyze_blocked_tasks(
        modified,
        entity_id=entity_id,
        event_type=event_type,
        reason=reason,
        parameters=parameters,
    )

    before_states = {entry["entity_id"]: entry["state"] for entry in before["states"]}
    state_changes = []
    for entry in after["states"]:
        eid = entry["entity_id"]
        previous = before_states.get(eid)
        if previous != entry["state"]:
            state_changes.append(
                {"entity_id": eid, "before": previous, "after": entry["state"]}
            )

    return {
        **after,
        "changes": [dict(change) for change in changes],
        "state_changes": state_changes,
        "before_states": before["states"],
        "assumptions": [
            *after["assumptions"],
            "Change sets are hypothetical: the committed plan is never modified.",
        ],
    }
