"""Deterministic scoring and ranking of recovery options (Person 3, Milestone 3).

This module is **pure**: it never mutates the scenario, the option list, caller
metrics or weights, and never changes recovery-option ids or evaluation
verdicts. Identical input yields an identical report.

Formula (lower is better)
--------------------------
``Score = w1 * Delay + w2 * Cost + w3 * Risk + w4 * ResourceStrain + w5 * DisruptionCount``

over the five canonical metrics, in formula order:

=====================  ====================================================
``delay``              (w1) derived from :mod:`rippleback.forecast` as
                       ``delay_delta`` -- forecast critical-path length minus
                       baseline, in relative ``duration_units``/``steps``
                       (signed; negative means a shorter critical path).
``cost``               (w2) **no source in the repository** -- caller-supplied
                       only.
``risk``               (w3) **no source in the repository** -- caller-supplied
                       only.
``resource_strain``    (w4) derived from :mod:`rippleback.workload` as total
                       overload ``sum(capacity shortfall)`` in ``work_units``;
                       ``None`` (missing) for scenarios without staffing data.
                       Scenario-level: identical for every option.
``disruption_count``   (w5) **no source in the repository** (the engine models
                       a single disruption) -- caller-supplied only.
=====================  ====================================================

Default weights and why
-----------------------
``delay: 1.0``; ``cost``, ``risk``, ``resource_strain``, ``disruption_count``:
``0.0``. ``delay`` is the only metric the repository can supply for *every*
feasible option, so the default score is always computable and honest. The
other defaults are a **documented policy, not silent zero-filling**: the report
always echoes the resolved ``weights`` and lists ``excluded_metrics`` (weight
zero), and a caller who supplies real cost/risk/strain/count data can raise
those weights. Weights are unitless; when several metrics are weighted the
score mixes scales, so scores compare options **within one call only**.

Missing-data policy
-------------------
A metric with a **nonzero weight** that has no value (absent, or explicit
``None``) makes the option **unscorable**: ``scorable: False``, ``score:
None``, and a ``reasons`` entry naming the metric and weight. Missing data is
never treated as zero. A metric with weight ``0.0`` may be absent -- it is
reported in ``missing_metrics``/``excluded_metrics`` but does not block
scoring. Malformed values (non-numbers, ``bool``, non-finite ``NaN``/``inf``,
negative values for the non-negative metrics, negative or unknown weights)
raise :class:`ScoringError`.

Feasibility gate
----------------
:func:`rank_options` validates each option's change set with
:func:`rippleback.constraints.validate_hard_constraints` **before** any score
is computed. Infeasible options are excluded with their constraint
``violations`` preserved verbatim, ``score: None``, and never appear in
``ranked`` or the ``recommendation``. An id-derived option whose edge does not
exist in the committed plan is excluded the same way (no-op recoveries are
never recommended). When no feasible, scorable option remains the report
carries ``outcome: "no_feasible_recovery"`` with ``recommendation: None``.
Equal scores tie-break by ascending ``option_id`` (stable and total).
"""

from __future__ import annotations

import copy
import math
from typing import Any

from rippleback.constraints import ConstraintError, validate_hard_constraints
from rippleback.evaluation import EvaluationError, parse_option_id
from rippleback.forecast import recalculate_delivery_forecast
from rippleback.workload import rebalance_workload

METRIC_DELAY = "delay"
METRIC_COST = "cost"
METRIC_RISK = "risk"
METRIC_RESOURCE_STRAIN = "resource_strain"
METRIC_DISRUPTION_COUNT = "disruption_count"

#: Canonical metric order, matching the documented formula (w1..w5).
METRIC_KEYS = (
    METRIC_DELAY,
    METRIC_COST,
    METRIC_RISK,
    METRIC_RESOURCE_STRAIN,
    METRIC_DISRUPTION_COUNT,
)

#: Metrics that cannot be negative once supplied (``delay`` is signed).
NON_NEGATIVE_METRICS = frozenset(
    {METRIC_COST, METRIC_RISK, METRIC_RESOURCE_STRAIN, METRIC_DISRUPTION_COUNT}
)

DEFAULT_WEIGHTS: dict[str, float] = {
    METRIC_DELAY: 1.0,
    METRIC_COST: 0.0,
    METRIC_RISK: 0.0,
    METRIC_RESOURCE_STRAIN: 0.0,
    METRIC_DISRUPTION_COUNT: 0.0,
}

OUTCOME_RANKED = "ranked"
OUTCOME_NO_FEASIBLE_RECOVERY = "no_feasible_recovery"

ACTION_REMOVE_DEPENDENCY = "remove_dependency"


class ScoringError(ValueError):
    """Raised for malformed scoring requests (bad weights, metrics, or options)."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _resolve_weights(weights: dict[str, Any] | None) -> dict[str, float]:
    """Validate ``weights`` and merge them over :data:`DEFAULT_WEIGHTS`."""
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    if not isinstance(weights, dict):
        raise ScoringError(
            f"weights must be a dict of metric->number, "
            f"got {type(weights).__name__}"
        )
    unknown = [key for key in weights if key not in METRIC_KEYS]
    if unknown:
        raise ScoringError(
            f"unknown weight metric(s) {sorted(map(str, unknown))}; "
            f"allowed: {list(METRIC_KEYS)}"
        )
    resolved = dict(DEFAULT_WEIGHTS)
    for key, value in weights.items():
        if not _is_number(value):
            raise ScoringError(
                f"weight '{key}' must be a number, got {type(value).__name__}"
            )
        number = float(value)
        if not math.isfinite(number):
            raise ScoringError(f"weight '{key}' must be finite, got {value!r}")
        if number < 0.0:
            raise ScoringError(
                f"weight '{key}' must be non-negative; the formula is "
                f"lower-is-better, so a negative weight would invert it"
            )
        resolved[key] = number
    return resolved


def _validate_metrics(metrics: Any) -> dict[str, float]:
    """Validate supplied metrics; returns present values as floats (None = absent)."""
    if not isinstance(metrics, dict):
        raise ScoringError(
            f"metrics must be a dict of metric->number, "
            f"got {type(metrics).__name__}"
        )
    unknown = [key for key in metrics if key not in METRIC_KEYS]
    if unknown:
        raise ScoringError(
            f"unknown metric(s) {sorted(map(str, unknown))}; "
            f"allowed: {list(METRIC_KEYS)}"
        )
    present: dict[str, float] = {}
    for key in METRIC_KEYS:
        if key not in metrics or metrics[key] is None:
            continue
        value = metrics[key]
        if not _is_number(value):
            raise ScoringError(
                f"metric '{key}' must be a number, got {type(value).__name__}"
            )
        number = float(value)
        if not math.isfinite(number):
            raise ScoringError(f"metric '{key}' must be finite, got {value!r}")
        if key in NON_NEGATIVE_METRICS and number < 0.0:
            raise ScoringError(f"metric '{key}' must be non-negative, got {number}")
        present[key] = number
    return present


def score_option(
    metrics: dict[str, Any], weights: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Score one option's metrics against validated weights (lower is better).

    Returns a stable report: ``scorable``, ``score`` (total, or ``None`` when
    unscorable), the resolved ``weights``, per-metric weighted ``components``
    (``None`` when unscorable), the supplied ``metrics``, ``missing_metrics``
    (no value supplied), ``excluded_metrics`` (weight ``0.0``), and ``reasons``
    explaining any unscorable outcome.

    Raises:
        ScoringError: for malformed inputs -- non-dict metrics/weights,
            unknown metric or weight keys, non-numbers, ``bool``, ``NaN`` or
            ``inf``, negative values for non-negative metrics, or negative
            weights.
    """
    present = _validate_metrics(metrics)
    resolved = _resolve_weights(weights)

    missing = [key for key in METRIC_KEYS if key not in present]
    excluded = [key for key in METRIC_KEYS if resolved[key] == 0.0]
    unusable = [key for key in missing if resolved[key] != 0.0]
    reasons = [
        f"metric '{key}' has nonzero weight {resolved[key]:g} but no value was "
        "supplied; the option is unscorable rather than scored with a "
        "fabricated zero."
        for key in unusable
    ]

    components: dict[str, float] | None = None
    score: float | None = None
    if not unusable:
        components = {
            key: resolved[key] * present.get(key, 0.0) for key in METRIC_KEYS
        }
        score = math.fsum(components[key] for key in METRIC_KEYS)

    return {
        "scorable": not unusable,
        "score": score,
        "weights": resolved,
        "components": components,
        "metrics": copy.deepcopy(metrics),
        "missing_metrics": missing,
        "excluded_metrics": excluded,
        "reasons": reasons,
    }


def _resource_strain(scenario: dict[str, Any]) -> float | None:
    """Total overload across resources, or ``None`` when staffing data is absent."""
    if not scenario.get("resources"):
        return None
    report = rebalance_workload(
        scenario, expected_plan_version=scenario["version"]
    )
    return float(
        sum(
            -resource["free_capacity"]
            for resource in report["resources"]
            if resource["free_capacity"] < 0
        )
    )


def _changes_for_option(
    option: dict[str, Any], scenario: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Resolve an option's change set.

    Returns ``(changes, derived, edge_ok)``. Explicit ``option["changes"]`` is
    used verbatim (``derived`` ``False``, ``edge_ok`` always ``True``);
    otherwise the ``option_id`` is parsed as
    ``drop-dependency:{dependent}->{prerequisite}`` into the equivalent
    ``remove_dependency`` change (``derived`` ``True``), and ``edge_ok`` says
    whether that edge exists in the committed plan.

    Raises:
        ScoringError: for a malformed ``option_id`` or a non-list ``changes``.
    """
    if "changes" in option:
        raw = option["changes"]
        if not isinstance(raw, list):
            raise ScoringError(
                f"option {option.get('option_id')!r}: 'changes' must be a list, "
                f"got {type(raw).__name__}"
            )
        return copy.deepcopy(raw), False, True

    option_id = option["option_id"]
    try:
        dependent, prerequisite = parse_option_id(option_id)
    except EvaluationError as exc:
        raise ScoringError(str(exc)) from exc
    edge_exists = any(
        (dependency["dependent"], dependency["prerequisite"])
        == (dependent, prerequisite)
        for dependency in scenario["dependencies"]
    )
    if not edge_exists:
        return [], True, False
    return (
        [
            {
                "action": ACTION_REMOVE_DEPENDENCY,
                "dependent": dependent,
                "prerequisite": prerequisite,
            }
        ],
        True,
        True,
    )


def _validate_options(options: Any) -> list[dict[str, Any]]:
    """Structural checks for the option list; returns it unchanged."""
    if not isinstance(options, list) or not options:
        raise ScoringError(
            f"options must be a non-empty list of option dicts, "
            f"got {type(options).__name__}"
        )
    seen: set[str] = set()
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            raise ScoringError(
                f"options[{index}] must be a dict, got {type(option).__name__}"
            )
        option_id = option.get("option_id")
        if not isinstance(option_id, str) or not option_id.strip():
            raise ScoringError(
                f"options[{index}] requires a non-empty string 'option_id'"
            )
        if option_id in seen:
            raise ScoringError(
                f"duplicate option_id {option_id!r}; ranking requires unique ids"
            )
        seen.add(option_id)
        raw_metrics = option.get("metrics")
        if raw_metrics is not None and not isinstance(raw_metrics, dict):
            raise ScoringError(
                f"option {option_id!r}: 'metrics' must be a dict, "
                f"got {type(raw_metrics).__name__}"
            )
        if raw_metrics is not None:
            _validate_metrics(raw_metrics)
    return options


def _entry(
    option_id: str,
    *,
    rank: int | None,
    feasible: bool,
    scorable: bool,
    score: float | None,
    components: dict[str, float] | None,
    metrics: dict[str, Any],
    missing_metrics: list[str],
    violations: list[dict[str, Any]],
    reasons: list[str],
) -> dict[str, Any]:
    """Uniform per-option entry (same keys for ranked and excluded options)."""
    return {
        "option_id": option_id,
        "rank": rank,
        "feasible": feasible,
        "scorable": scorable,
        "score": score,
        "components": components,
        "metrics": metrics,
        "missing_metrics": missing_metrics,
        "violations": violations,
        "reasons": reasons,
    }


def rank_options(
    scenario: dict[str, Any],
    options: list[dict[str, Any]],
    weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Feasibility-gate, score, and rank recovery options (lower score wins).

    Each option needs a unique, non-empty ``option_id``. Its change set is
    ``option["changes"]`` when present, otherwise derived from the id
    (``drop-dependency:{dependent}->{prerequisite}``, which must match a
    committed edge -- an id that matches nothing is excluded as a no-op
    recovery). The change set is validated with
    :func:`rippleback.constraints.validate_hard_constraints` **before** any
    score is computed: infeasible options are excluded with their violations
    preserved and are never scored or recommended. ``delay`` is derived from
    :func:`rippleback.forecast.recalculate_delivery_forecast` and
    ``resource_strain`` from :func:`rippleback.workload.rebalance_workload`;
    ``option["metrics"]`` (caller data such as cost or risk) overrides derived
    values. Equal scores tie-break by ascending ``option_id``.

    Raises:
        ConstraintError: malformed scenario or malformed custom changes (from
            the Milestone 2 API, propagated unchanged).
        ScoringError: malformed options list, option ids, metrics, or weights.
    """
    _validate_options(options)
    resolved_weights = _resolve_weights(weights)

    # Baseline validation via the Milestone 2 API: raises ConstraintError for a
    # malformed scenario before any metric is derived.
    validate_hard_constraints(scenario, changes=[])
    strain = _resource_strain(scenario)

    ranked: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    feasible_count = 0

    for option in options:  # input order for deterministic exclusion output
        option_id = option["option_id"]
        changes, derived, edge_ok = _changes_for_option(option, scenario)
        caller_metrics = option.get("metrics") or {}

        if derived and not edge_ok:
            excluded.append(
                _entry(
                    option_id,
                    rank=None,
                    feasible=False,
                    scorable=False,
                    score=None,
                    components=None,
                    metrics=copy.deepcopy(caller_metrics),
                    missing_metrics=[],
                    violations=[],
                    reasons=[
                        f"excluded: option_id {option_id!r} does not match any "
                        "committed dependency edge, so it would be a no-op "
                        "recovery; no-op recoveries are never recommended."
                    ],
                )
            )
            continue

        gate = validate_hard_constraints(scenario, changes=changes)
        if gate["feasible"]:
            feasible_count += 1

        if not gate["feasible"]:
            excluded.append(
                _entry(
                    option_id,
                    rank=None,
                    feasible=False,
                    scorable=False,
                    score=None,
                    components=None,
                    metrics=copy.deepcopy(caller_metrics),
                    missing_metrics=[],
                    violations=gate["violations"],
                    reasons=[
                        "excluded: the proposed changes are infeasible; an "
                        "infeasible option is never scored or recommended."
                    ],
                )
            )
            continue

        derived: dict[str, Any] = {}
        forecast = recalculate_delivery_forecast(scenario, changes=changes)
        if forecast["feasible"] and forecast["delay_delta"] is not None:
            derived[METRIC_DELAY] = float(forecast["delay_delta"])
        else:
            excluded.append(
                _entry(
                    option_id,
                    rank=None,
                    feasible=False,
                    scorable=False,
                    score=None,
                    components=None,
                    metrics=copy.deepcopy(caller_metrics),
                    missing_metrics=[],
                    violations=gate["violations"],
                    reasons=[
                        "excluded: the delay metric could not be derived from "
                        "the forecast for these changes."
                    ],
                )
            )
            continue
        if strain is not None:
            derived[METRIC_RESOURCE_STRAIN] = strain

        metrics = {**derived, **caller_metrics}
        score_report = score_option(metrics, resolved_weights)

        if not score_report["scorable"]:
            excluded.append(
                _entry(
                    option_id,
                    rank=None,
                    feasible=True,
                    scorable=False,
                    score=None,
                    components=None,
                    metrics=copy.deepcopy(metrics),
                    missing_metrics=score_report["missing_metrics"],
                    violations=[],
                    reasons=score_report["reasons"],
                )
            )
            continue

        ranked.append(
            _entry(
                option_id,
                rank=None,
                feasible=True,
                scorable=True,
                score=score_report["score"],
                components=score_report["components"],
                metrics=copy.deepcopy(metrics),
                missing_metrics=score_report["missing_metrics"],
                violations=[],
                reasons=[],
            )
        )

    ranked.sort(key=lambda entry: (entry["score"], entry["option_id"]))
    for position, entry in enumerate(ranked, start=1):
        entry["rank"] = position

    scorable_count = len(ranked)
    outcome = OUTCOME_RANKED if ranked else OUTCOME_NO_FEASIBLE_RECOVERY
    recommendation = ranked[0]["option_id"] if ranked else None

    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "plan_version": scenario["version"],
        "simulated": True,
        "outcome": outcome,
        "weights": resolved_weights,
        "feasible_count": feasible_count,
        "scorable_count": scorable_count,
        "recommendation": recommendation,
        "ranked": ranked,
        "excluded": excluded,
        "assumptions": [
            "Score = w1*delay + w2*cost + w3*risk + w4*resource_strain + "
            "w5*disruption_count over the echoed weights; lower is better and "
            "equal scores tie-break by ascending option_id.",
            "Feasibility is gated with rippleback.constraints."
            "validate_hard_constraints before scoring; infeasible options keep "
            "their violations, are never scored, and never appear in the "
            "recommendation.",
            "delay derives from the forecast delay_delta (relative critical-"
            "path units) and resource_strain from workload overload "
            "(work_units); caller-supplied metrics override derived values.",
            "cost, risk, and disruption_count have no source in the "
            "repository; their default weight is 0.0 and they contribute only "
            "when a caller supplies both a value and a nonzero weight.",
            "Weights are unitless; scores are comparable only within one call.",
        ],
        "limitations": [
            "The repository supplies no cost or risk data; defaults zero those "
            "weights instead of fabricating values, and a nonzero weight "
            "without data yields an unscorable option, never a zero-filled "
            "score.",
            "resource_strain is scenario-level (identical for every option -- "
            "workload ignores dependencies) and is absent for scenarios "
            "without staffing data.",
            "No delivery date is predicted: delay is a relative critical-path "
            "delta under a single-disruption model, never wall-clock time.",
            "Read-only: options, the committed plan, evaluation verdicts, and "
            "recovery-option ids are unchanged.",
        ],
    }
