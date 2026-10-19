"""Relative-duration recovery scheduling engine (Person 3).

Given a scenario and an optional recovery change set (or recovery ``option_id``)
plus an optional disruption, this module produces a **hypothetical, read-only
schedule**: which tasks can begin now, which must wait for prerequisites, which
may run in parallel, an execution order, and — when every task declares a
duration — earliest start/finish times and a projected completion duration in a
single relative unit.

This module is **pure**: it never mutates the committed scenario, its entities,
dependencies, statuses, or version, and identical input yields identical output.

Scope honesty
-------------
The repository models **no calendar**: no project start date, timezone, working
days, working hours, or holidays exist in any contract or fixture
(``docs/data-contract.md`` §10), so this engine schedules in **relative time**
only. ``t = 0`` is the moment of analysis; every reported time is a relative
offset in the schedule's unit (``duration_units`` by default), never a
wall-clock date. Calendar scheduling is therefore deliberately not implemented.

Durations are read from entity ``duration`` fields (or explicit caller overrides
in ``scheduling_config``) and are **never invented**: when any entity lacks a
duration, the report still contains the dependency ordering, readiness groups,
and parallel layers, but start/finish times and the completion duration are
``null`` with ``missing_data`` explaining exactly what is absent
(``outcome: "insufficient_duration_data"``).

Resources are deliberately unconstrained: the workload model's ``work_units``
are abstract capacity with no time dimension, so parallel groups assume
unlimited parallel execution subject only to hard dependencies.

How the schedule is computed
----------------------------
* The dependency graph is the existing :func:`rippleback.graph.build_graph`
  result; **hard** edges (``requires_completion`` / ``requires_arrival_by``)
  constrain ordering, ``benefits_from`` edges never do.
* A task's earliest start is the maximum earliest finish of its hard
  prerequisites (``0`` when it has none); its waiting time is exactly the gap
  between its prerequisites' finishes and its start, so execution time
  (``duration``) and waiting time (``waiting_time``) are always reported
  separately.
* ``requires_arrival_by`` ``required_buffer_minutes`` is honored **only** when
  the caller explicitly declares the duration unit as minutes via
  ``scheduling_config["duration_unit"]``; otherwise the buffer is preserved,
  reported on the edges, and explicitly *not* added to start times (mixing a
  minutes buffer into unitless ``duration_units`` would fabricate precision).
* Entities with committed status ``done`` are complete at ``t = 0`` and are
  never restarted or re-blocked. Committed ``blocked`` entities and (when a
  disruption is given) blocker-triage ``blocked``/``external`` entities are not
  scheduled as executable; their finish time is unknown and the completion
  duration covers only schedulable work.
* A proposed change set is gated first with
  :func:`rippleback.constraints.validate_hard_constraints`; a change set that
  creates a cycle, a dangling reference, or a constraint violation is reported
  ``infeasible`` with its violations and **no schedule** — an invalid proposal
  is never ordered, ranked, or presented as an alternative.

The topological order produced here is *a* valid order with deterministic
tie-breaking (lexicographic within layers), not the only or "optimal" one.

Complexity: ordering, grouping, and layering are ``O(V + E)``; the critical
path and triage reuse the existing ``O(V + E)`` engines.
"""

from __future__ import annotations

import copy
from typing import Any

from rippleback.constraints import ConstraintError, validate_hard_constraints
from rippleback.critical_path import critical_path as _critical_path
from rippleback.evaluation import EvaluationError, parse_option_id
from rippleback.graph import (
    EDGE_CONSTRAINT_PARAMETERS,
    EDGE_HARD,
    build_graph,
)
from rippleback.triage import (
    STATE_BLOCKED,
    STATE_DONE,
    STATE_EXTERNAL,
    TriageError,
    apply_changes,
    recompute_with_changes,
)

DEFAULT_UNIT = "duration_units"
MINUTES_UNIT = "minutes"

#: Recognized ``constraint_parameters`` key on ``requires_arrival_by`` edges.
BUFFER_MINUTES_KEY = "required_buffer_minutes"

#: ``scheduling_config`` keys the engine understands; anything else is rejected.
ALLOWED_CONFIG_KEYS = frozenset({"duration_unit", "durations"})

GROUP_COMPLETED = "completed"
GROUP_READY = "ready_now"
GROUP_WAITING = "waiting"
GROUP_BLOCKED = "blocked"

OUTCOME_SCHEDULED = "scheduled"
OUTCOME_INSUFFICIENT = "insufficient_duration_data"
OUTCOME_INFEASIBLE = "infeasible"

ERROR_INVALID_CONFIG = "INVALID_CONFIG"
ERROR_INVALID_REQUEST = "INVALID_REQUEST"

TRIAGE_BLOCKED_STATES = frozenset({STATE_BLOCKED, STATE_EXTERNAL})

_SUBSTITUTE_ID_PREFIX = "substitute:"
_SUBSTITUTE_ID_HINT = (
    "uses the substitute id format, which the recovery-option parser does not "
    "support; pass that option's explicit 'changes' list instead."
)


class SchedulerError(ValueError):
    """Raised for invalid scheduling requests (bad config, changes, or inputs)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── Input handling ────────────────────────────────────────────────────────────


def _changes_from_option_id(
    scenario: dict[str, Any], option_id: str
) -> list[dict[str, Any]]:
    """Parse a ``drop-dependency:`` recovery option id into a removal change.

    Malformed ids, unknown entity ids, and ids that do not match a committed
    dependency edge are rejected loudly — an unknown option is an error, never
    a silent no-op. Substitute ids (``substitute:...``) are not parseable and
    are rejected with guidance to pass explicit ``changes``.
    """
    if isinstance(option_id, str) and option_id.startswith(_SUBSTITUTE_ID_PREFIX):
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            f"option_id {option_id!r} {_SUBSTITUTE_ID_HINT}",
        )
    try:
        dependent, prerequisite = parse_option_id(option_id)
    except EvaluationError as exc:
        raise SchedulerError(ERROR_INVALID_REQUEST, str(exc)) from exc

    by_id = {entity["id"]: entity for entity in scenario["entities"]}
    unknown = [name for name in (dependent, prerequisite) if name not in by_id]
    if unknown:
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            f"option_id {option_id!r} references unknown entity id(s): {unknown}.",
        )
    edge_exists = any(
        (dependency["dependent"], dependency["prerequisite"])
        == (dependent, prerequisite)
        for dependency in scenario["dependencies"]
    )
    if not edge_exists:
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            f"option_id {option_id!r} does not match any committed dependency "
            f"edge: '{dependent}' requires '{prerequisite}'.",
        )
    return [
        {
            "action": "remove_dependency",
            "dependent": dependent,
            "prerequisite": prerequisite,
        }
    ]


def _resolve_durations(
    scenario: dict[str, Any], config: dict[str, Any] | None
) -> tuple[dict[str, float] | None, str | None, list[str]]:
    """Merge entity durations with explicit caller overrides.

    Returns ``(durations, unit, missing)``: ``durations`` maps every entity id
    to a positive duration and ``unit`` is the declared schedule unit, or both
    are ``None`` when any entity lacks a duration (``missing`` then lists those
    entities in scenario order). Durations are never invented.
    """
    overrides: dict[str, Any] = {}
    unit: str | None = None
    if config is not None:
        if not isinstance(config, dict):
            raise SchedulerError(
                ERROR_INVALID_CONFIG,
                f"scheduling_config must be a dict, got {type(config).__name__}",
            )
        unknown_keys = sorted(set(config) - ALLOWED_CONFIG_KEYS)
        if unknown_keys:
            raise SchedulerError(
                ERROR_INVALID_CONFIG,
                f"scheduling_config has unknown key(s) {unknown_keys}; "
                f"allowed: {sorted(ALLOWED_CONFIG_KEYS)}",
            )
        declared_unit = config.get("duration_unit")
        if declared_unit is not None:
            if not isinstance(declared_unit, str) or not declared_unit.strip():
                raise SchedulerError(
                    ERROR_INVALID_CONFIG,
                    "scheduling_config['duration_unit'] must be a non-empty string",
                )
            unit = declared_unit.strip()
        overrides = config.get("durations", {})
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise SchedulerError(
                ERROR_INVALID_CONFIG,
                "scheduling_config['durations'] must be a dict mapping entity "
                f"id to a positive number, got {type(overrides).__name__}",
            )

    by_id = {entity["id"]: entity for entity in scenario["entities"]}
    for entity_id, value in overrides.items():
        if entity_id not in by_id:
            raise SchedulerError(
                ERROR_INVALID_CONFIG,
                f"scheduling_config['durations'] names unknown entity id "
                f"{entity_id!r}; available: {sorted(by_id)}",
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
        ):
            raise SchedulerError(
                ERROR_INVALID_CONFIG,
                f"scheduling_config['durations'][{entity_id!r}] must be a "
                f"positive number (bool excluded); got {value!r}",
            )

    merged: dict[str, float] = {}
    missing: list[str] = []
    for entity in scenario["entities"]:
        entity_id = entity["id"]
        if entity_id in overrides:
            merged[entity_id] = float(overrides[entity_id])
            continue
        # Entity durations are validated positive by validate_scenario (which
        # the validation gate runs before scheduling), so this is a safe read.
        value = entity.get("duration")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        ):
            merged[entity_id] = float(value)
        else:
            missing.append(entity_id)

    if missing:
        return None, None, missing
    return merged, unit or DEFAULT_UNIT, []


def _resolve_buffers(
    scenario: dict[str, Any], unit: str | None
) -> tuple[dict[tuple[str, str], float], list[str]]:
    """Collect ``required_buffer_minutes`` buffers on hard edges.

    Returns ``(buffers, unapplied_notes)``. Buffers are added to start times
    only when the schedule unit is explicitly declared minutes; otherwise they
    are preserved and reported but not mixed into unitless durations.
    ``buffers`` maps ``(prerequisite, dependent)`` to minutes and is empty when
    the unit is not minutes.
    """
    buffers: dict[tuple[str, str], float] = {}
    unapplied: list[str] = []
    graph = build_graph(scenario)  # raises GraphError on structurally bad input
    for dependency in scenario["dependencies"]:
        prerequisite = dependency["prerequisite"]
        dependent = dependency["dependent"]
        if not graph.edges[prerequisite, dependent][EDGE_HARD]:
            continue
        parameters = dependency.get(EDGE_CONSTRAINT_PARAMETERS)
        if not isinstance(parameters, dict) or BUFFER_MINUTES_KEY not in parameters:
            continue
        value = parameters[BUFFER_MINUTES_KEY]
        note = (
            f"required_buffer_minutes={value!r} on '{dependent}' requires "
            f"'{prerequisite}'"
        )
        if unit != MINUTES_UNIT:
            unapplied.append(note)
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
        ):
            raise SchedulerError(
                ERROR_INVALID_CONFIG,
                f"{note} must be a non-negative number (bool excluded); it "
                f"cannot be applied in unit {unit!r}.",
            )
        buffers[(prerequisite, dependent)] = float(value)
    return buffers, unapplied


# ── Core scheduling ───────────────────────────────────────────────────────────


def _hard_prerequisites(
    scenario: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (dependent -> hard prerequisites, prerequisite -> hard dependents).

    Prerequisite lists keep scenario dependency order; dependent lists are
    sorted so the Kahn pass is deterministic. ``build_graph`` validates the
    structure and raises :class:`~rippleback.graph.GraphError` on bad input.
    """
    graph = build_graph(scenario)
    hard: dict[str, list[str]] = {entity["id"]: [] for entity in scenario["entities"]}
    for dependency in scenario["dependencies"]:
        prerequisite = dependency["prerequisite"]
        dependent = dependency["dependent"]
        if graph.edges[prerequisite, dependent][EDGE_HARD]:
            hard[dependent].append(prerequisite)
    successors: dict[str, list[str]] = {entity_id: [] for entity_id in hard}
    for dependent, prereqs in hard.items():
        for prerequisite in prereqs:
            successors[prerequisite].append(dependent)
    for node in successors:
        successors[node].sort()
    return hard, successors


def _soft_prerequisites(scenario: dict[str, Any]) -> dict[str, list[str]]:
    """dependent -> sorted soft (``benefits_from``) prerequisites."""
    soft: dict[str, list[str]] = {entity["id"]: [] for entity in scenario["entities"]}
    for dependency in scenario["dependencies"]:
        if dependency.get("dependency_type") == "benefits_from":
            soft[dependency["dependent"]].append(dependency["prerequisite"])
    for node in soft:
        soft[node] = sorted(soft[node])
    return soft


def _initial_group(entity: dict[str, Any], triage_state: str | None) -> str:
    """Classify an entity's scheduling group before topological propagation."""
    if entity.get("status") == STATE_DONE:
        return GROUP_COMPLETED
    if (
        entity.get("status") == STATE_BLOCKED
        or (triage_state is not None and triage_state in TRIAGE_BLOCKED_STATES)
    ):
        return GROUP_BLOCKED
    return GROUP_READY  # provisional; refined to waiting/ready by the Kahn pass


def _compute_schedule(
    scenario: dict[str, Any],
    *,
    durations: dict[str, float] | None,
    unit: str | None,
    blocked_reasons: dict[str, str],
    triage_states: dict[str, str] | None,
    buffers: dict[tuple[str, str], float],
) -> dict[str, Any]:
    """Compute the relative schedule for one (already validated) plan.

    Deterministic Kahn pass over hard edges with lexicographic tie-breaking.
    ``durations`` is ``None`` for an ordering-only schedule (missing data);
    start/finish times are then ``null`` and no completion duration is claimed.
    """
    order = [entity["id"] for entity in scenario["entities"]]
    index_of = {entity_id: i for i, entity_id in enumerate(order)}
    by_id = {entity["id"]: entity for entity in scenario["entities"]}
    hard, successors = _hard_prerequisites(scenario)
    soft = _soft_prerequisites(scenario)
    report = _critical_path(scenario)
    on_path = set(report["path"])

    groups_by_id = {
        entity_id: _initial_group(by_id[entity_id], (triage_states or {}).get(entity_id))
        for entity_id in order
    }
    indegree = {entity_id: len(hard[entity_id]) for entity_id in order}
    ready = sorted(entity_id for entity_id in order if indegree[entity_id] == 0)

    entries: dict[str, dict[str, Any]] = {}
    while ready:
        current = ready.pop(0)
        status = by_id[current].get("status")
        prereqs = hard[current]
        group = groups_by_id[current]

        if group == GROUP_COMPLETED:
            layer: int | None = 0
            start: float | None = 0.0
            finish: float | None = 0.0
            waiting: float | None = 0.0
            reason = (
                f"'{current}' is already completed (status 'done'); it is "
                f"complete at the relative origin and is never restarted."
            )
        elif group == GROUP_BLOCKED:
            layer = None
            start = finish = waiting = None
            reason = blocked_reasons.get(
                current,
                f"'{current}' is blocked and is not scheduled as executable.",
            )
        else:
            unresolved = [
                prerequisite
                for prerequisite in prereqs
                if entries[prerequisite]["group"] in (GROUP_WAITING, GROUP_BLOCKED)
            ]
            if unresolved:
                group = GROUP_WAITING
                layer = None
                start = finish = waiting = None
                reason = (
                    f"'{current}' is waiting on unfinished hard "
                    f"prerequisite(s): {unresolved}."
                )
            else:
                group = GROUP_READY
                predecessor_finish = (
                    max((entries[p]["earliest_finish"] or 0.0) for p in prereqs)
                    if prereqs
                    else 0.0
                )
                start_candidate = max(
                    [
                        predecessor_finish,
                        *[
                            (entries[p]["earliest_finish"] or 0.0)
                            + buffers.get((p, current), 0.0)
                            for p in prereqs
                        ],
                    ]
                )
                scheduled_layers = [
                    entries[p]["layer"]
                    for p in prereqs
                    if entries[p]["group"] == GROUP_READY
                    and entries[p]["layer"] is not None
                ]
                layer = 1 + (max(scheduled_layers) if scheduled_layers else 0)
                if durations is None:
                    start = finish = waiting = None
                    reason = (
                        f"'{current}' has no unfinished hard prerequisites; "
                        f"ordering computed, but no duration is declared, so "
                        f"no start time is produced."
                    )
                else:
                    start = start_candidate
                    finish = start + durations[current]
                    waiting = start - predecessor_finish
                    if not prereqs:
                        reason = (
                            f"'{current}' has no hard prerequisites and is "
                            f"ready at the relative origin."
                        )
                    else:
                        reason = (
                            f"'{current}' starts when its hard prerequisites "
                            f"finish (waiting {waiting:g} {unit or 'units'}, "
                            f"executing {durations[current]:g} "
                            f"{unit or 'units'})."
                        )

        groups_by_id[current] = group
        entries[current] = {
            "entity_id": current,
            "name": by_id[current].get("name"),
            "status": status,
            "triage_state": (triage_states or {}).get(current),
            "group": group,
            "layer": layer,
            "duration": None if durations is None else durations.get(current),
            "earliest_start": start,
            "earliest_finish": finish,
            "waiting_time": waiting,
            "hard_prerequisites": list(prereqs),
            "soft_prerequisites": soft[current],
            "on_critical_path": current in on_path,
            "reason": reason,
        }

        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()

    entries_list = [entries[entity_id] for entity_id in order]
    scheduled = sorted(
        (entry for entry in entries_list if entry["group"] == GROUP_READY),
        key=lambda entry: (entry["layer"], index_of[entry["entity_id"]]),
    )
    execution_order = [entry["entity_id"] for entry in scheduled]

    layers: dict[int, list[str]] = {}
    for entry in scheduled:
        layers.setdefault(entry["layer"], []).append(entry["entity_id"])
    parallel_groups = [
        {"layer": layer, "entity_ids": sorted(layers[layer], key=index_of.get)}
        for layer in sorted(layers)
    ]

    groups = {
        group_name: [
            entry["entity_id"] for entry in entries_list if entry["group"] == group_name
        ]
        for group_name in (GROUP_COMPLETED, GROUP_READY, GROUP_WAITING, GROUP_BLOCKED)
    }

    unfinished = [entry for entry in entries_list if entry["status"] != STATE_DONE]
    if not unfinished:
        completion_duration: float | None = 0.0
        explanation = "Every task is already completed; no work remains."
    elif scheduled and durations is not None:
        completion_duration = max(entry["earliest_finish"] for entry in scheduled)
        explanation = (
            "Projected makespan over schedulable remaining work (relative "
            "units from the moment of analysis)."
        )
    elif scheduled:
        completion_duration = None
        explanation = (
            "Tasks are ordered, but no duration is declared, so no "
            "completion duration can be produced."
        )
    else:
        completion_duration = None
        explanation = (
            "No task is schedulable (all remaining work is blocked or "
            "waiting), so no completion duration can be produced."
        )
    unfinishable = groups[GROUP_WAITING] + groups[GROUP_BLOCKED]
    completion = {
        "duration": completion_duration,
        "unit": unit,
        "covers_all_tasks": not unfinishable,
        "excluded_tasks": unfinishable,
        "explanation": explanation,
    }

    return {
        "unit": unit,
        "schedule": entries_list,
        "groups": groups,
        "execution_order": execution_order,
        "parallel_groups": parallel_groups,
        "critical_path": report,
        "completion": completion,
    }


# ── Comparison ────────────────────────────────────────────────────────────────


def _offset_changes(
    baseline_entries: list[dict[str, Any]], proposed_entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-task start/finish offsets (or groups) that differ between schedules."""
    proposed_by_id = {entry["entity_id"]: entry for entry in proposed_entries}
    changes: list[dict[str, Any]] = []
    for before in baseline_entries:
        after = proposed_by_id.get(before["entity_id"])
        if after is None:
            continue
        if (
            before["earliest_start"] == after["earliest_start"]
            and before["earliest_finish"] == after["earliest_finish"]
            and before["group"] == after["group"]
        ):
            continue
        delta_start: float | None = None
        delta_finish: float | None = None
        if (
            before["earliest_start"] is not None
            and after["earliest_start"] is not None
        ):
            delta_start = after["earliest_start"] - before["earliest_start"]
        if (
            before["earliest_finish"] is not None
            and after["earliest_finish"] is not None
        ):
            delta_finish = after["earliest_finish"] - before["earliest_finish"]
        changes.append(
            {
                "entity_id": before["entity_id"],
                "baseline_group": before["group"],
                "proposed_group": after["group"],
                "baseline_start": before["earliest_start"],
                "proposed_start": after["earliest_start"],
                "delta_start": delta_start,
                "baseline_finish": before["earliest_finish"],
                "proposed_finish": after["earliest_finish"],
                "delta_finish": delta_finish,
            }
        )
    return changes


def _compare_schedules(
    baseline: dict[str, Any],
    proposed: dict[str, Any],
    *,
    state_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare a baseline schedule with a proposed recovery schedule."""
    newly_blocked = [
        change["entity_id"]
        for change in state_changes
        if change["after"] in TRIAGE_BLOCKED_STATES
        and change["before"] not in TRIAGE_BLOCKED_STATES
    ]
    newly_unblocked = [
        change["entity_id"]
        for change in state_changes
        if change["before"] in TRIAGE_BLOCKED_STATES
        and change["after"] not in TRIAGE_BLOCKED_STATES
    ]

    baseline_completion = baseline["completion"]["duration"]
    proposed_completion = proposed["completion"]["duration"]
    comparable = (
        baseline["unit"] is not None
        and proposed["unit"] is not None
        and baseline["unit"] == proposed["unit"]
        and baseline_completion is not None
        and proposed_completion is not None
    )
    incomparable_reason: str | None = None
    if not comparable:
        if baseline["unit"] is None or proposed["unit"] is None:
            incomparable_reason = (
                "Task durations are missing, so no completion duration exists "
                "on one or both sides; only ordering and parallel groups are "
                "comparable."
            )
        else:
            incomparable_reason = (
                "One side has no completion duration (all remaining work is "
                "blocked or waiting there), so a completion delta would not "
                "compare like with like."
            )
    excluded_changed = (
        baseline["completion"]["excluded_tasks"]
        != proposed["completion"]["excluded_tasks"]
    )

    def _max_group(block: dict[str, Any]) -> int:
        return max(
            (len(group["entity_ids"]) for group in block["parallel_groups"]),
            default=0,
        )

    return {
        "comparable": comparable,
        "incomparable_reason": incomparable_reason,
        "baseline_unit": baseline["unit"],
        "proposed_unit": proposed["unit"],
        "baseline_execution_order": baseline["execution_order"],
        "proposed_execution_order": proposed["execution_order"],
        "execution_order_changed": (
            baseline["execution_order"] != proposed["execution_order"]
        ),
        "baseline_critical_path": baseline["critical_path"]["path"],
        "proposed_critical_path": proposed["critical_path"]["path"],
        "critical_path_changed": (
            baseline["critical_path"]["path"] != proposed["critical_path"]["path"]
        ),
        "baseline_completion_duration": baseline_completion,
        "proposed_completion_duration": proposed_completion,
        "completion_delta": (
            proposed_completion - baseline_completion if comparable else None
        ),
        "baseline_excluded_tasks": baseline["completion"]["excluded_tasks"],
        "proposed_excluded_tasks": proposed["completion"]["excluded_tasks"],
        "excluded_tasks_changed": excluded_changed,
        "task_offset_changes": _offset_changes(
            baseline["schedule"], proposed["schedule"]
        ),
        "newly_blocked": newly_blocked,
        "newly_unblocked": newly_unblocked,
        "parallelism": {
            "baseline_layer_count": len(baseline["parallel_groups"]),
            "proposed_layer_count": len(proposed["parallel_groups"]),
            "baseline_max_group_size": _max_group(baseline),
            "proposed_max_group_size": _max_group(proposed),
        },
    }


# ── Public engine ─────────────────────────────────────────────────────────────


def _blocked_reasons(
    scenario: dict[str, Any], states: dict[str, str] | None
) -> dict[str, str]:
    """Explain, per entity, why it is not scheduled as executable."""
    reasons: dict[str, str] = {}
    for entity in scenario["entities"]:
        entity_id = entity["id"]
        state = (states or {}).get(entity_id)
        if state in TRIAGE_BLOCKED_STATES:
            reasons[entity_id] = (
                f"'{entity_id}' is {state} after the disruption "
                f"(blocker-triage state), so it is not scheduled as executable."
            )
        elif entity["status"] == STATE_BLOCKED:
            reasons[entity_id] = (
                f"'{entity_id}' has committed status 'blocked' and no modeled "
                f"unblock cause, so it is not scheduled."
            )
    return reasons


def generate_recovery_schedule(
    scenario: dict[str, Any],
    *,
    changes: list[dict[str, Any]] | None = None,
    option_id: str | None = None,
    disrupted_entity_id: str | None = None,
    scheduling_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a hypothetical, read-only schedule for a scenario.

    With no ``changes``/``option_id``, the report describes the committed plan's
    schedule. With a change set (the ``rippleback.triage.apply_changes`` shape)
    or a ``drop-dependency:`` recovery ``option_id``, the report additionally
    contains the committed plan's ``baseline`` schedule and a ``comparison``
    (orders, critical paths, completion delta when comparable, per-task offset
    changes, newly blocked/unblocked tasks, and parallelism changes).

    When ``disrupted_entity_id`` is given, readiness is recomputed with blocker
    triage (default ``service_outage`` event) against the **modified** graph, so
    a recovery that removes the cause of a blockage shows as hypothetical
    unblocked work — clearly distinct from the committed state.

    ``scheduling_config`` optionally declares ``duration_unit`` (a label;
    ``"minutes"`` additionally honors ``required_buffer_minutes`` arrival
    buffers) and ``durations`` (explicit caller-supplied per-entity positive
    durations that override entity ``duration`` fields for this hypothetical
    report).

    Raises:
        SchedulerError: for invalid requests — both ``changes`` and
            ``option_id`` given, ``changes`` not a list, a malformed change or
            option id, an unknown ``disrupted_entity_id``, or an invalid
            ``scheduling_config``. Malformed scenarios/changes surface as
            :class:`rippleback.constraints.ConstraintError` (wrapped here), and
            a structurally invalid committed graph raises
            :class:`~rippleback.graph.GraphError`.

    A well-formed change set that violates graph rules or a hard constraint is
    an expected analysis outcome, not an error: the report comes back with
    ``feasible`` ``False``, ``outcome: "infeasible"``, the stable violations,
    and ``null`` schedule/comparison fields — an infeasible proposal is never
    ordered or recommended.
    """
    if changes is not None and option_id is not None:
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            "provide either 'changes' or 'option_id', not both",
        )
    if changes is not None and not isinstance(changes, list):
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            f"changes must be a list of change dicts, got {type(changes).__name__}",
        )

    by_id = {entity["id"]: entity for entity in scenario["entities"]}
    proposed: list[dict[str, Any]]
    option_prerequisite: str | None = None
    if option_id is not None:
        proposed = _changes_from_option_id(scenario, option_id)
        option_prerequisite = proposed[0]["prerequisite"]
    elif changes is not None:
        proposed = copy.deepcopy(changes)
    else:
        proposed = []
    has_changes = bool(proposed)

    if disrupted_entity_id is not None and disrupted_entity_id not in by_id:
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            f"unknown disrupted_entity_id {disrupted_entity_id!r}; "
            f"available: {sorted(by_id)}",
        )
    if (
        option_prerequisite is not None
        and disrupted_entity_id is not None
        and option_prerequisite != disrupted_entity_id
    ):
        raise SchedulerError(
            ERROR_INVALID_REQUEST,
            f"option_id {option_id!r} targets prerequisite "
            f"'{option_prerequisite}', which does not match "
            f"disrupted_entity_id '{disrupted_entity_id}'.",
        )
    resolved_disruption = (
        disrupted_entity_id
        if disrupted_entity_id is not None
        else option_prerequisite
    )

    # Duration and buffer resolution read only entity data and explicit caller
    # overrides. Scenario validation happens inside the feasibility gate below;
    # durations from a validated scenario are guaranteed positive numbers.
    durations, unit, missing = _resolve_durations(scenario, scheduling_config)
    buffers, unapplied_buffers = _resolve_buffers(scenario, unit)

    missing_data: list[str] = []
    if missing:
        missing_data.append(
            f"No duration is declared for {len(missing)} entit"
            f"{'y is' if len(missing) == 1 else 'ies are'}: {missing}. Durations "
            f"are never invented, so earliest start/finish times and a "
            f"completion duration are not produced; ordering, readiness "
            f"groups, and parallel layers are. Declare entity 'duration' "
            f"values or pass scheduling_config['durations'] to enable a "
            f"duration-aware schedule."
        )

    def _assumptions() -> list[str]:
        base = [
            "The schedule is relative: t=0 is the moment of analysis. The "
            "repository models no calendar, project start date, or working "
            "hours, so all times are relative offsets in the reported unit, "
            "never wall-clock dates.",
            "Ordering follows hard requires edges only (requires_completion / "
            "requires_arrival_by); benefits_from edges are reported as soft "
            "prerequisites and never constrain start times.",
            "A task's earliest start is the maximum earliest finish of its "
            "hard prerequisites (+ any honored arrival buffer); tasks with no "
            "unfinished hard prerequisites are ready at the relative origin. "
            "The topological order is one valid order with deterministic "
            "lexicographic tie-breaking, not the only or optimal schedule.",
            "Committed 'done' tasks are already complete at t=0 and are never "
            "restarted or re-blocked; committed 'blocked' tasks are never "
            "scheduled as executable. 'in_progress' tasks are scheduled with "
            "their full declared duration from t=0; partial progress is not "
            "modeled.",
            "Change sets (when given) are applied to a copy of the plan with "
            "rippleback.triage.apply_changes semantics; the committed plan, "
            "statuses, and version are never modified.",
        ]
        if resolved_disruption is not None:
            base.append(
                f"Readiness after the disruption on '{resolved_disruption}' "
                f"uses blocker triage (default service_outage event): "
                f"blocked/external entities are not scheduled, healthy/at_risk "
                f"entities are. A recovery that removes the blocking path is "
                f"recomputed against the modified graph — a hypothetical "
                f"result, distinct from the committed state."
            )
        if unit == MINUTES_UNIT:
            base.append(
                "The caller declared the duration unit as minutes via "
                "scheduling_config['duration_unit'], so requires_arrival_by "
                "buffers (required_buffer_minutes) are honored in start times."
            )
        if isinstance(scheduling_config, dict) and scheduling_config.get("durations"):
            base.append(
                "Caller-supplied scheduling_config['durations'] overrides "
                "entity-declared durations for this hypothetical report only."
            )
        return base

    def _limitations() -> list[str]:
        base = [
            "No calendar scheduling: no project start date, timezone, working "
            "days, working hours, or holidays exist in the data, so calendar "
            "dates cannot be derived and deadline optimization or resource "
            "leveling is not claimed.",
            "No resource-constrained scheduling: workload 'work_units' are "
            "abstract capacity with no time dimension, and the data models no "
            "skills, availability, or assignments-as-schedule; parallel groups "
            "assume unlimited parallel execution subject only to hard "
            "dependencies.",
            "The critical path shown is the existing structural "
            "longest-hard-chain analysis: it counts declared durations of "
            "completed tasks as well and is not a time forecast; the "
            "completion duration covers only unfinished, schedulable work.",
        ]
        if unapplied_buffers:
            base.append(
                "requires_arrival_by buffers were preserved but not added to "
                "start times because the schedule unit is not minutes "
                "(mixing a minutes buffer into unitless durations would "
                "fabricate precision): " + "; ".join(sorted(unapplied_buffers))
            )
        if missing:
            base.append(
                "Task durations are missing on one or more entities, so this "
                "is an ordering-only schedule: no start/finish times, no "
                "completion duration, and no duration-based comparison."
            )
        base.append(
            "Blocked tasks have no scheduled finish; the completion duration "
            "covers only schedulable work (see completion.covers_all_tasks)."
        )
        base.append(
            "Read-only and hypothetical: the committed plan is never modified, "
            "no schedule is committed, and business readiness of a recovery "
            "change is not assessed here."
        )
        return base

    # ── Feasibility gate (reused validator) ────────────────────────────────
    try:
        validation = validate_hard_constraints(scenario, changes=proposed)
    except ConstraintError as exc:
        raise SchedulerError(ERROR_INVALID_REQUEST, str(exc)) from exc

    # ── Triage for readiness (reused engine) ───────────────────────────────
    triage_states: dict[str, str] | None = None
    baseline_states: dict[str, str] | None = None
    state_changes: list[dict[str, Any]] = []
    if resolved_disruption is not None:
        try:
            triage_report = recompute_with_changes(
                scenario,
                entity_id=resolved_disruption,
                changes=proposed,
            )
        except TriageError as exc:
            raise SchedulerError(exc.code, str(exc)) from exc
        triage_states = {
            entry["entity_id"]: entry["state"] for entry in triage_report["states"]
        }
        baseline_states = {
            entry["entity_id"]: entry["state"]
            for entry in triage_report["before_states"]
        }
        state_changes = triage_report["state_changes"]

    baseline_block: dict[str, Any] | None = None
    if has_changes:
        baseline_validation = validate_hard_constraints(scenario, changes=[])
        baseline_schedule = _compute_schedule(
            scenario,
            durations=durations,
            unit=unit,
            blocked_reasons=_blocked_reasons(scenario, baseline_states),
            triage_states=baseline_states,
            buffers=buffers,
        )
        baseline_block = {
            "feasible": baseline_validation["feasible"],
            "violations": baseline_validation["violations"],
            **baseline_schedule,
        }

    if not validation["feasible"]:
        violations = validation["violations"]
        return {
            "scenario_id": scenario["scenario_id"],
            "plan_id": scenario["plan_id"],
            "plan_version": scenario["version"],
            "simulated": True,
            "disrupted_entity_id": resolved_disruption,
            "option_id": option_id,
            "changes": proposed,
            "feasible": False,
            "outcome": OUTCOME_INFEASIBLE,
            "error": {
                "code": violations[0]["code"],
                "message": violations[0]["message"],
            },
            "unit": None,
            "schedule": None,
            "groups": None,
            "execution_order": None,
            "parallel_groups": None,
            "critical_path": None,
            "completion": None,
            "baseline": baseline_block,
            "comparison": None,
            "violations": violations,
            "missing_data": missing_data,
            "assumptions": _assumptions(),
            "limitations": [
                *_limitations(),
                "The proposed change set is infeasible, so no schedule, "
                "execution order, or completion time is reported for it.",
            ],
        }

    working = {**scenario, "dependencies": apply_changes(scenario, proposed)}
    proposed_schedule = _compute_schedule(
        working,
        durations=durations,
        unit=unit,
        blocked_reasons=_blocked_reasons(working, triage_states),
        triage_states=triage_states,
        buffers=buffers,
    )

    comparison: dict[str, Any] | None = None
    if baseline_block is not None:
        comparison = _compare_schedules(
            baseline_block, proposed_schedule, state_changes=state_changes
        )

    outcome = OUTCOME_SCHEDULED if durations is not None else OUTCOME_INSUFFICIENT
    limitations = _limitations()
    if comparison is not None and comparison["excluded_tasks_changed"]:
        limitations.append(
            "The baseline and proposed schedules exclude different tasks "
            "(their blocked/waiting sets differ), so completion_delta "
            "compares the makespan of the schedulable work on each side, not "
            "an identical work set; read it together with "
            "baseline_excluded_tasks / proposed_excluded_tasks."
        )
    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "disrupted_entity_id": resolved_disruption,
        "option_id": option_id,
        "changes": proposed,
        "feasible": True,
        "outcome": outcome,
        "error": None,
        "unit": unit,
        "schedule": proposed_schedule["schedule"],
        "groups": proposed_schedule["groups"],
        "execution_order": proposed_schedule["execution_order"],
        "parallel_groups": proposed_schedule["parallel_groups"],
        "critical_path": proposed_schedule["critical_path"],
        "completion": proposed_schedule["completion"],
        "baseline": baseline_block,
        "comparison": comparison,
        "violations": [],
        "missing_data": missing_data,
        "assumptions": _assumptions(),
        "limitations": limitations,
    }
