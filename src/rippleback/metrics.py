"""Derived scoring inputs: cost, risk, and open-disruption count.

The scoring engine is deliberately pure: it scores only what the caller hands
it and treats a nonzero weight with a missing metric as *unscorable*. This
module supplies real inputs from configured sources and resolves weights
safely, so a pristine demo with no data keeps the historical defaults
(``delay=1.0``, everything else ``0.0``) and never becomes unscorable.

Formulas (documented in ``docs/DECISIONS.md``)
----------------------------------------------
**cost** — ``COST_PER_CHANGE`` × change count, plus the remaining rework cost of
every affected, unfinished entity. An entity's cost is ``metadata.cost`` when
a PM field supplies it, otherwise ``effort × COST_RATE_PER_WORK_UNIT``. If any
affected unfinished entity has no computable cost, the whole option is
"not computable" and the cost metric is omitted (never zero-filled).

**risk** — severity-weighted, recency-decayed event load over the option's
blast radius (directly changed entities plus all transitive dependents):
``Σ severity_weight × 0.5 ** (age_days / 30)`` using incidents and open
disruptions. Recency is measured against the **newest recorded event**, not
wall-clock time, so identical data always produces identical scores.

**disruption_count** — number of open disruptions inside the same blast radius.

Affected-entity definition for both cost and risk: the ``dependent`` and
``prerequisite`` of every dependency change, the entity of every reassignment,
plus every transitive dependent of those entities.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rippleback.config import load_settings
from rippleback.db import Disruption, IncidentHistory
from rippleback.evaluation import EvaluationError, parse_option_id
from rippleback.scoring import DEFAULT_WEIGHTS

SEVERITY_WEIGHTS = {
    "low": 1.0,
    "medium": 2.0,
    "high": 3.0,
    "critical": 4.0,
    "unknown": 1.0,
}
HALF_LIFE_DAYS = 30.0
METRIC_KEYS = ("delay", "cost", "risk", "resource_strain", "disruption_count")


def _option_changes(scenario: dict[str, Any], option: dict[str, Any]) -> list[dict[str, Any]] | None:
    changes = option.get("changes")
    if isinstance(changes, list):
        return changes
    option_id = option.get("option_id")
    if isinstance(option_id, str) and option_id.startswith("drop-dependency:"):
        try:
            dependent, prerequisite = parse_option_id(option_id)
        except EvaluationError:
            return None
        return [
            {
                "action": "remove_dependency",
                "dependent": dependent,
                "prerequisite": prerequisite,
            }
        ]
    return None


def affected_entities(
    scenario: dict[str, Any], changes: list[dict[str, Any]]
) -> set[str]:
    """Directly changed entities plus every transitive dependent."""
    direct: set[str] = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        action = change.get("action")
        if action == "reassign":
            direct.add(change.get("entity_id"))
        else:
            direct.add(change.get("dependent"))
            direct.add(change.get("prerequisite"))
    dependents: dict[str, set[str]] = {}
    for dependency in scenario["dependencies"]:
        dependents.setdefault(dependency["prerequisite"], set()).add(
            dependency["dependent"]
        )
    queue = list(direct)
    seen = set(direct)
    while queue:
        current = queue.pop()
        for dependent in dependents.get(current, ()):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return seen


def derive_cost(
    scenario: dict[str, Any], changes: list[dict[str, Any]], affected: set[str]
) -> float | None:
    settings = load_settings()
    rate = settings.cost_rate_per_work_unit
    total = 0.0
    for entity in scenario["entities"]:
        if entity["id"] not in affected or entity["status"] == "done":
            continue
        metadata = entity.get("metadata") or {}
        declared = metadata.get("cost")
        if isinstance(declared, (int, float)) and not isinstance(declared, bool):
            total += float(declared)
            continue
        effort = entity.get("effort")
        if rate is None or not isinstance(effort, (int, float)) or isinstance(effort, bool):
            return None
        total += float(effort) * rate
    total += settings.cost_per_change * len(changes)
    return round(total, 6)


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed


def _event_rows(
    session: Session, scenario_id: str
) -> list[tuple[str | None, str, str]]:
    """``(entity_id, severity, occurred_at)`` for incidents + open disruptions."""
    rows: list[tuple[str | None, str, str]] = []
    for incident in session.execute(
        select(IncidentHistory).where(IncidentHistory.scenario_id == scenario_id)
    ).scalars().all():
        rows.append((incident.entity_id, incident.severity, incident.occurred_at))
    for disruption in session.execute(
        select(Disruption).where(
            Disruption.scenario_id == scenario_id, Disruption.status == "open"
        )
    ).scalars().all():
        rows.append((disruption.entity_id, disruption.severity, disruption.recorded_at))
    return rows


def derive_risk(
    affected: set[str], events: list[tuple[str | None, str, str]]
) -> float | None:
    if not events:
        return None
    timed: list[tuple[str | None, str, datetime]] = []
    for entity_id, severity, occurred_at in events:
        parsed = _parse_time(occurred_at)
        if parsed is not None:
            timed.append((entity_id, severity, parsed))
    if not timed:
        return None
    reference = max(item[2] for item in timed)
    total = 0.0
    for entity_id, severity, parsed in timed:
        if entity_id not in affected:
            continue
        age_days = max(0.0, (reference - parsed).total_seconds() / 86400.0)
        decay = 0.5 ** (age_days / HALF_LIFE_DAYS)
        total += SEVERITY_WEIGHTS.get(severity, 1.0) * decay
    return round(total, 6)


def derive_disruption_count(
    affected: set[str], open_rows: list[tuple[str | None, str, str]]
) -> int | None:
    if not open_rows:
        return None
    return sum(1 for entity_id, _severity, _at in open_rows if entity_id in affected)


def derive_metrics(
    scenario: dict[str, Any], options: list[dict[str, Any]]
) -> dict[str, dict[str, float]]:
    """Compute whatever metrics the configured sources can actually support."""
    from rippleback.db import session_scope

    with session_scope() as session:
        events = _event_rows(session, scenario["scenario_id"])
        open_disruptions = session.execute(
            select(Disruption).where(
                Disruption.scenario_id == scenario["scenario_id"],
                Disruption.status == "open",
            )
        ).scalars().all()
        open_rows = [
            (row.entity_id, row.severity, row.recorded_at) for row in open_disruptions
        ]
    metrics: dict[str, dict[str, float]] = {}
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("option_id")
        if not isinstance(option_id, str):
            continue
        changes = _option_changes(scenario, option)
        if changes is None:
            metrics[option_id] = {}
            continue
        affected = affected_entities(scenario, changes)
        values: dict[str, float] = {}
        cost = derive_cost(scenario, changes, affected)
        if cost is not None:
            values["cost"] = cost
        risk = derive_risk(affected, events)
        if risk is not None:
            values["risk"] = risk
        count = derive_disruption_count(affected, open_rows)
        if count is not None:
            values["disruption_count"] = float(count)
        metrics[option_id] = values
    return metrics


def _source_configured(scenario: dict[str, Any], metric: str) -> bool:
    """Whether the scenario has any data source for ``metric`` at all."""
    if metric == "cost":
        if load_settings().cost_rate_per_work_unit is not None:
            return True
        return any(
            isinstance((entity.get("metadata") or {}).get("cost"), (int, float))
            and not isinstance((entity.get("metadata") or {}).get("cost"), bool)
            for entity in scenario["entities"]
        )
    if metric in {"risk", "disruption_count"}:
        from rippleback.db import session_scope

        with session_scope() as session:
            has_incidents = (
                session.execute(
                    select(IncidentHistory.id)
                    .where(IncidentHistory.scenario_id == scenario["scenario_id"])
                    .limit(1)
                ).first()
                is not None
            )
            has_disruptions = (
                session.execute(
                    select(Disruption.id)
                    .where(
                        Disruption.scenario_id == scenario["scenario_id"],
                        Disruption.status == "open",
                    )
                    .limit(1)
                ).first()
                is not None
            )
        if metric == "disruption_count":
            return has_disruptions
        return has_incidents or has_disruptions
    return False


def resolve_weights(
    scenario: dict[str, Any], options: list[dict[str, Any]]
) -> tuple[dict[str, float], dict[str, str]]:
    """Resolve effective weights plus a per-metric source label.

    Policy: an env weight always wins; otherwise a metric auto-enables at 1.0
    only when its source is configured **and every option has a computed
    value**. Otherwise it stays at the historical default (0.0 for everything
    except delay). ``resource_strain`` remains opt-in (env only) because
    capacity data is caller/demo supplied.
    """
    settings = load_settings()
    weights = copy.deepcopy(DEFAULT_WEIGHTS)
    sources = {key: "default" for key in METRIC_KEYS}
    metrics = derive_metrics(scenario, options)
    env_weights = {
        "delay": settings.score_weight_delay,
        "cost": settings.score_weight_cost,
        "risk": settings.score_weight_risk,
        "resource_strain": settings.score_weight_strain,
        "disruption_count": settings.score_weight_disruption,
    }
    for metric in METRIC_KEYS:
        configured = env_weights[metric]
        if configured is not None:
            weights[metric] = configured
            sources[metric] = "env"
            continue
        if metric == "delay":
            sources[metric] = "default"
            continue
        if metric == "resource_strain":
            weights[metric] = 0.0
            sources[metric] = "disabled_opt_in"
            continue
        if not _source_configured(scenario, metric):
            weights[metric] = 0.0
            sources[metric] = "disabled_no_source"
            continue
        has_everywhere = bool(options) and all(
            isinstance(option, dict)
            and isinstance(option.get("option_id"), str)
            and metric in metrics.get(option.get("option_id"), {})
            for option in options
        )
        if has_everywhere:
            weights[metric] = 1.0
            sources[metric] = "data"
        else:
            weights[metric] = 0.0
            sources[metric] = "disabled_incomplete_data"
    return weights, sources
