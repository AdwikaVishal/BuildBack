"""Deterministic delivery forecasts in relative duration units.

Person 3, Milestone 1. Given a scenario and a proposed recovery change set,
this module compares the committed plan's **baseline** critical path with the
**forecast** critical path after the change set is applied to a copy of the
plan.

This module is **pure**: it never mutates the committed scenario, its entities,
dependencies, or version, and identical input yields identical output.

What the numbers mean
---------------------
Lengths are the same relative critical-path durations reported by
:mod:`rippleback.critical_path` -- ``duration_units`` when fixtures declare
durations, ``steps`` when they do not. ``delay_delta`` is::

    forecast critical-path length - baseline critical-path length

i.e. a **whole-plan critical-path duration comparison** before and after the
change set. It is *not* delay relative to a disrupted baseline, *not* task-level
delay, and *not* a calendar delivery date: the repository models no project
calendar, start date, or working hours.

Feasibility vs input errors
---------------------------
Invalid *requests* raise :class:`ForecastError`: conflicting inputs, a
``changes`` value that is not a list or is malformed (via
:func:`rippleback.triage.apply_changes`), a malformed or unknown ``option_id``,
an unknown ``disrupted_entity_id``, or an ``option_id`` whose prerequisite
contradicts ``disrupted_entity_id``.

A well-formed change set that produces an **invalid graph** (cycle, unknown
reference, self-dependency) is an expected analysis outcome, not a caller
error: the report comes back with ``feasible`` ``False``, an ``error`` carrying
the stable :mod:`rippleback.graph` error code, and ``forecast_path`` /
``forecast_length`` / ``delay_delta`` set to ``None`` -- an infeasible change
set is never reported as a valid forecast. A malformed *input* scenario
propagates :class:`~rippleback.graph.GraphError` from the baseline computation,
as in every other engine.

Inputs
------
``changes`` is the primary input: a list in the ``rippleback.triage``
``apply_changes`` shape (``{"action": "remove_dependency" | "add_dependency",
"dependent": ..., "prerequisite": ...}``). When ``option_id`` is given instead,
it is parsed with :func:`rippleback.evaluation.parse_option_id` and converted
to the equivalent ``remove_dependency`` change; malformed or unknown option ids
are rejected loudly. Providing both ``changes`` and ``option_id`` is
ambiguous and raises :class:`ForecastError`. Providing neither returns a
baseline-equals-forecast report (``delay_delta`` ``0.0``).
"""

from __future__ import annotations

import copy
from typing import Any

from rippleback.critical_path import critical_path
from rippleback.evaluation import (
    ACTION_REMOVE_DEPENDENCY,
    EvaluationError,
    parse_option_id,
)
from rippleback.graph import GraphError
from rippleback.triage import TriageError, apply_changes


class ForecastError(ValueError):
    """Raised for invalid forecast requests (bad inputs, not infeasible graphs)."""


def _report(
    scenario: dict[str, Any],
    *,
    disrupted_entity_id: str | None,
    option_id: str | None,
    changes: list[dict[str, Any]],
    baseline: dict[str, Any],
    feasible: bool,
    error: dict[str, str] | None,
    forecast: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the stable report shape (same keys for every outcome)."""
    assumptions = [
        "Baseline is the committed plan's critical path; the forecast is the "
        "critical path after applying the change set to a copy of the plan. "
        "The committed plan, its statuses, and its version are never modified.",
        "'delay_delta' compares total critical-path duration before and after "
        "the change set; it is not delay relative to a disrupted baseline and "
        "not a calendar delivery date.",
        "Critical path follows hard requires edges only; benefits_from "
        "edges never define ordering.",
        "Changes are applied with rippleback.triage.apply_changes semantics; "
        "removing an edge that does not exist is a no-op.",
    ]
    if option_id is not None:
        assumptions.append(
            f"option_id {option_id!r} was parsed with the recovery-option "
            f"parser into a '{ACTION_REMOVE_DEPENDENCY}' change."
        )
    limitations = [
        "No wall-clock dates are modeled: lengths are relative critical-path "
        "durations in the reported unit, never calendar delivery dates.",
        "The forecast reflects only the proposed change set; disruption "
        "state, resource capacity, workload, and slack are not modeled.",
        "delay_delta is a whole-plan critical-path comparison; it does not "
        "measure per-task delay or delivery-date risk.",
    ]
    if not feasible:
        limitations.append(
            "The proposed change set produces an invalid dependency graph, "
            "so no forecast or delay_delta is reported."
        )
    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "disrupted_entity_id": disrupted_entity_id,
        "option_id": option_id,
        "changes": changes,
        "feasible": feasible,
        "error": error,
        "unit": baseline["unit"],
        "baseline_path": baseline["path"],
        "baseline_length": baseline["length"],
        "baseline_edges": baseline["edges"],
        "forecast_path": None if forecast is None else forecast["path"],
        "forecast_length": None if forecast is None else forecast["length"],
        "forecast_edges": None if forecast is None else forecast["edges"],
        "delay_delta": (
            None if forecast is None else forecast["length"] - baseline["length"]
        ),
        "assumptions": assumptions,
        "limitations": limitations,
    }


def _changes_from_option_id(
    scenario: dict[str, Any], option_id: str
) -> list[dict[str, Any]]:
    """Parse a recovery option id into the equivalent ``remove_dependency`` change.

    Raises :class:`ForecastError` for a malformed id (via
    :func:`rippleback.evaluation.parse_option_id`), for ids naming unknown
    entities, and for ids that do not match any committed dependency edge --
    an unknown option is an error, never a silent no-op.
    """
    try:
        dependent, prerequisite = parse_option_id(option_id)
    except EvaluationError as exc:
        raise ForecastError(str(exc)) from exc

    by_id = {entity["id"]: entity for entity in scenario["entities"]}
    unknown = [name for name in (dependent, prerequisite) if name not in by_id]
    if unknown:
        raise ForecastError(
            f"option_id {option_id!r} references unknown entity id(s): {unknown}."
        )

    edge_exists = any(
        (dependency["dependent"], dependency["prerequisite"])
        == (dependent, prerequisite)
        for dependency in scenario["dependencies"]
    )
    if not edge_exists:
        raise ForecastError(
            f"option_id {option_id!r} does not match any committed dependency "
            f"edge: '{dependent}' requires '{prerequisite}'."
        )
    return [
        {
            "action": ACTION_REMOVE_DEPENDENCY,
            "dependent": dependent,
            "prerequisite": prerequisite,
        }
    ]


def recalculate_delivery_forecast(
    scenario: dict[str, Any],
    *,
    disrupted_entity_id: str | None = None,
    changes: list[dict[str, Any]] | None = None,
    option_id: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, read-only baseline-vs-forecast critical-path report.

    The committed ``scenario`` is never modified. ``changes`` (the
    :func:`rippleback.triage.apply_changes` shape) is applied to a copy of the
    plan and the resulting graph is validated before any forecast is reported.
    Alternatively ``option_id`` is parsed with
    :func:`rippleback.evaluation.parse_option_id` and converted to the
    equivalent ``remove_dependency`` change; when ``option_id`` is given
    without ``disrupted_entity_id``, the option's prerequisite is reported as
    the disrupted entity.

    Raises:
        ForecastError: for invalid *requests* -- both ``changes`` and
            ``option_id`` given, ``changes`` not a list, a malformed change
            list (via ``apply_changes``), a malformed or unknown ``option_id``,
            an unknown ``disrupted_entity_id``, or an ``option_id`` whose
            prerequisite contradicts ``disrupted_entity_id``.

    A well-formed change set that yields an invalid graph (cycle, unknown
    reference, self-dependency) does **not** raise: the report carries
    ``feasible`` ``False`` plus the graph error code and ``None`` forecast
    fields. A malformed input scenario propagates
    :class:`~rippleback.graph.GraphError` from the baseline computation.
    """
    if changes is not None and option_id is not None:
        raise ForecastError("provide either 'changes' or 'option_id', not both")
    if changes is not None and not isinstance(changes, list):
        raise ForecastError(
            f"changes must be a list of change dicts, got {type(changes).__name__}"
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

    if disrupted_entity_id is not None and disrupted_entity_id not in by_id:
        raise ForecastError(
            f"unknown disrupted_entity_id {disrupted_entity_id!r}; "
            f"available: {sorted(by_id)}"
        )
    if (
        option_prerequisite is not None
        and disrupted_entity_id is not None
        and option_prerequisite != disrupted_entity_id
    ):
        raise ForecastError(
            f"option_id {option_id!r} targets prerequisite "
            f"'{option_prerequisite}', which does not match "
            f"disrupted_entity_id '{disrupted_entity_id}'."
        )
    resolved_disruption = (
        disrupted_entity_id if disrupted_entity_id is not None else option_prerequisite
    )

    # Baseline: computed from the committed plan. A malformed input scenario
    # propagates GraphError here, as in every other engine.
    baseline = critical_path(scenario)

    try:
        forecast_dependencies = apply_changes(scenario, proposed)
    except TriageError as exc:
        raise ForecastError(str(exc)) from exc
    forecast_scenario = {**scenario, "dependencies": forecast_dependencies}

    try:
        forecast = critical_path(forecast_scenario)
    except GraphError as exc:
        return _report(
            scenario,
            disrupted_entity_id=resolved_disruption,
            option_id=option_id,
            changes=proposed,
            baseline=baseline,
            feasible=False,
            error={"code": exc.code, "message": exc.message},
            forecast=None,
        )
    return _report(
        scenario,
        disrupted_entity_id=resolved_disruption,
        option_id=option_id,
        changes=proposed,
        baseline=baseline,
        feasible=True,
        error=None,
        forecast=forecast,
    )
