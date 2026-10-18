"""Recovery-option comparison with per-option delivery forecasts (Person 3, Milestone 4).

This module composes the Person 3 engines into one comparison report. It is
**pure**: it never mutates the scenario, the option list, or weights; identical
input yields an identical report, and key order is stable throughout.

What it adds to :func:`rippleback.scoring.rank_options`
-------------------------------------------------------
``compare_recovery_options`` returns the complete ranking report from
:func:`rippleback.scoring.rank_options` -- same ``outcome``, ``weights``,
``feasible_count``, ``scorable_count``, ``recommendation``, ranking rules,
no-op-exclusion policy, and error contract -- plus three things per call:

1. ``baseline`` -- the committed plan's full :func:`rippleback.critical_path`
   report, so the *total* critical-path duration (``baseline["length"]`` in
   ``baseline["unit"]``) sits next to each option's ``delay_delta``, which is a
   *difference* of totals, never a duration of its own.
2. ``forecast`` -- every ranked **and** excluded entry carries a fresh
   :func:`rippleback.forecast.recalculate_delivery_forecast` report for that
   option's change set (no disruption context: ``disrupted_entity_id`` and the
   forecast's ``option_id`` are ``None``), so baseline path, forecast path, and
   delta travel with the score.
3. ``no_op`` -- whether applying the option's change set yields the committed
   dependency plan unchanged (edge order ignored). Id-derived options whose
   edge does not exist are already excluded by the ranking as no-op recoveries;
   an explicit empty or ineffective change set can still rank (it is feasible
   and scorable), but it is never dressed up as an improvement: every no-op
   entry carries a limitation saying so, and when the ``recommendation``
   itself is a no-op the report says to keep the baseline.

Recommendation semantics
------------------------
``recommendation`` is exactly the ranking's: the top option that is both
feasible and scorable (ties break by ascending ``option_id``), or ``None`` when
no option qualifies (``outcome: "no_feasible_recovery"``). A feasible,
scorable no-op can be recommended -- the engine reports what the weights say --
but the report then limits itself to "keep the baseline" rather than
presenting the change as an improvement.

Errors
------
The comparison layer adds no new error types. Malformed options, weights, or
metrics raise :class:`rippleback.scoring.ScoringError`; a malformed scenario or
a malformed/unsupported change set raises
:class:`rippleback.constraints.ConstraintError` -- both propagated unchanged
from :func:`rank_options`. Well-formed inputs never raise from this layer: an
option whose changes produce an invalid graph surfaces in-band as
``feasible: False`` on the entry (constraint gate) with
``forecast["feasible"]: False`` and the stable graph error code.

Scope
-----
Read-only composition: recovery-option ids, ``evaluate_recovery_plan``
verdicts, committed plans, and the MCP surface are untouched; this module
registers no tools and offers no substitute-service candidates.
"""

from __future__ import annotations

import copy
from typing import Any

from rippleback.critical_path import critical_path
from rippleback.forecast import recalculate_delivery_forecast
from rippleback.scoring import (
    OUTCOME_NO_FEASIBLE_RECOVERY,
    _changes_for_option,
    rank_options,
)
from rippleback.triage import apply_changes

_NO_OP_ENTRY_NOTE = (
    "This option's change set leaves the committed dependency plan unchanged "
    "(no-op): its forecast equals the baseline, so it must not be presented as "
    "an improvement."
)


def _option_texts(option: dict[str, Any], key: str) -> list[Any]:
    """The option's own ``assumptions``/``limitations`` list, isolated by copy."""
    value = option.get(key)
    return copy.deepcopy(value) if isinstance(value, list) else []


def _plan_signature(dependencies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dependencies sorted by edge, so plan comparison ignores edge order."""
    return sorted(dependencies, key=lambda edge: (edge["dependent"], edge["prerequisite"]))


def _is_no_op(scenario: dict[str, Any], changes: list[dict[str, Any]]) -> bool:
    """True when applying ``changes`` yields exactly the committed plan."""
    return _plan_signature(apply_changes(scenario, changes)) == _plan_signature(
        scenario["dependencies"]
    )


def _augment_entry(
    entry: dict[str, Any], option: dict[str, Any], scenario: dict[str, Any]
) -> dict[str, Any]:
    """Attach ``forecast``, ``no_op``, ``assumptions``, and ``limitations``.

    The change set is re-resolved with the same private helper the ranking
    used, then forecast and no-op are recomputed from the committed scenario.
    Inputs are never mutated: the option's own text lists are deep-copied and
    the ranking entry is spread into a new dict.
    """
    changes, _, _ = _changes_for_option(option, scenario)
    forecast = recalculate_delivery_forecast(scenario, changes=changes)
    no_op = _is_no_op(scenario, changes)
    limitations = [
        *_option_texts(option, "limitations"),
        *forecast["limitations"],
    ]
    if no_op:
        limitations.append(_NO_OP_ENTRY_NOTE)
    return {
        **entry,
        "forecast": forecast,
        "no_op": no_op,
        "assumptions": [
            *_option_texts(option, "assumptions"),
            *forecast["assumptions"],
        ],
        "limitations": limitations,
    }


def compare_recovery_options(
    scenario: dict[str, Any],
    options: list[dict[str, Any]],
    weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Feasibility-gate, score, rank, and attach forecasts to recovery options.

    Validation, gating, scoring, and ranking are delegated wholesale to
    :func:`rippleback.scoring.rank_options` (including tie-breaks by ascending
    ``option_id`` and the ``no_feasible_recovery`` outcome), then every ranked
    and excluded entry is augmented with its ``forecast`` and ``no_op`` and the
    report gains the committed plan's ``baseline`` critical-path summary.

    Key order follows the ranking report and inserts ``baseline`` before
    ``ranked``. Each entry keeps the ranking entry's keys and appends
    ``forecast``, ``no_op``, ``assumptions`` (the option's own assumptions
    followed by the forecast's), and ``limitations`` (the option's own, the
    forecast's, plus a no-op note when the entry changes nothing).

    Raises:
        ConstraintError: malformed scenario or malformed/unsupported custom
            change set (Milestone 2 contract, propagated unchanged).
        ScoringError: malformed options list, option ids, metrics, or weights
            (Milestone 3 contract, propagated unchanged).
    """
    ranking = rank_options(scenario, options, weights)
    baseline = critical_path(scenario)

    option_by_id = {option["option_id"]: option for option in options}
    ranked = [
        _augment_entry(entry, option_by_id[entry["option_id"]], scenario)
        for entry in ranking["ranked"]
    ]
    excluded = [
        _augment_entry(entry, option_by_id[entry["option_id"]], scenario)
        for entry in ranking["excluded"]
    ]
    no_op_by_id = {entry["option_id"]: entry["no_op"] for entry in (*ranked, *excluded)}

    limitations = [
        *ranking["limitations"],
        "'baseline' reports the committed plan's total critical-path duration "
        "in 'unit'; each entry's forecast delay_delta is a difference of "
        "totals (forecast_length - baseline_length), not a duration and not a "
        "delay relative to a disruption.",
        "Lengths are relative critical-path durations (steps or "
        "duration_units), never wall-clock dates, and are not comparable "
        "across scenarios whose units differ.",
        "forecast.feasible says the option's changes yield a valid dependency "
        "graph; the entry's feasible flag (from rippleback.constraints) is "
        "what gates ranking, and an entry must be feasible and scorable to be "
        "recommended.",
    ]
    recommendation = ranking["recommendation"]
    if recommendation is not None and no_op_by_id[recommendation]:
        limitations.append(
            "The recommended option's change set does not modify the committed "
            "plan (no_op): treat it as 'keep the baseline', not as an "
            "improvement."
        )
    if ranking["outcome"] == OUTCOME_NO_FEASIBLE_RECOVERY:
        limitations.append(
            "No option was both feasible and scorable, so no recommendation "
            "is made; the committed plan remains the plan of record."
        )

    return {
        "scenario_id": ranking["scenario_id"],
        "plan_id": ranking["plan_id"],
        "plan_version": ranking["plan_version"],
        "simulated": True,
        "outcome": ranking["outcome"],
        "weights": ranking["weights"],
        "feasible_count": ranking["feasible_count"],
        "scorable_count": ranking["scorable_count"],
        "recommendation": recommendation,
        "baseline": baseline,
        "ranked": ranked,
        "excluded": excluded,
        "assumptions": [
            *ranking["assumptions"],
            "'baseline' is recomputed from the committed plan with "
            "rippleback.critical_path; its length is a total duration, while "
            "each entry's forecast delay_delta is the change that option's "
            "changes make to that total.",
            "Every ranked and excluded entry carries a forecast recomputed "
            "from the same scenario and that option's change set with "
            "rippleback.forecast.recalculate_delivery_forecast; forecasts "
            "carry no disruption context (disrupted_entity_id is None) and "
            "reuse the option's changes rather than its id.",
            "'no_op' is true exactly when applying the option's change set "
            "yields the committed dependency plan unchanged (edge order "
            "ignored); no-op options keep delay_delta 0.0 and are limited so "
            "they are never presented as improvements.",
            "The report never modifies the committed plan, the options, or "
            "the weights; recovery-option ids and evaluation verdicts are "
            "unchanged.",
        ],
        "limitations": limitations,
    }
