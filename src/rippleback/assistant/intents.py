"""Deterministic natural-language intent parsing for the RippleBack assistant.

This module maps a user message to one explicit *intent* plus extracted
parameters. It is deliberately dumb: it never computes anything that belongs
to a backend engine (no impact analysis, no scoring, no scheduling). It only
classifies the request and pulls out literal parameters (delay minutes,
severity, option ids, plan versions) that the orchestrator will pass to the
real MCP tools.

Entity and scenario *resolution* is the orchestrator's job, because it must be
done against live plan data from ``get_plan_state`` — never against hardcoded
id lists in this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Intent names ──────────────────────────────────────────────────────────────

PLAN_STATE = "plan_state"
SIMULATE_DISRUPTION = "simulate_disruption"
TRIAGE = "analyze_blocked_tasks"
ALTERNATIVES = "find_alternative_dependencies"
SUBSTITUTES = "find_substitute_options"
EVALUATE = "evaluate_recovery_plan"
CONSTRAINTS = "validate_hard_constraints"
COMPARE = "compare_recovery_options"
FORECAST = "recalculate_delivery_forecast"
WORKLOAD = "rebalance_workload"
SCHEDULE = "generate_recovery_schedule"
RECOVERY_WORKFLOW = "recovery_workflow"
HELP = "help"

#: Every intent the deterministic parser can produce.
ALL_INTENTS = (
    PLAN_STATE,
    SIMULATE_DISRUPTION,
    TRIAGE,
    ALTERNATIVES,
    SUBSTITUTES,
    EVALUATE,
    CONSTRAINTS,
    COMPARE,
    FORECAST,
    WORKLOAD,
    SCHEDULE,
    RECOVERY_WORKFLOW,
    HELP,
)

#: Intents that cannot run without a scenario id.
REQUIRES_SCENARIO = {
    PLAN_STATE,
    SIMULATE_DISRUPTION,
    TRIAGE,
    ALTERNATIVES,
    SUBSTITUTES,
    EVALUATE,
    CONSTRAINTS,
    COMPARE,
    FORECAST,
    WORKLOAD,
    SCHEDULE,
    RECOVERY_WORKFLOW,
}

#: Intents that cannot run without a target entity.
REQUIRES_ENTITY = {
    SIMULATE_DISRUPTION,
    TRIAGE,
    ALTERNATIVES,
    SUBSTITUTES,
    RECOVERY_WORKFLOW,
}

EVENT_TYPES = ("service_outage", "resource_unavailable", "delivery_delay", "transport_delay")
DELAY_EVENTS = frozenset({"delivery_delay", "transport_delay"})
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass
class Intent:
    """Parsed intent: a name, literal parameters, and optional clarification."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)
    clarification: str | None = None
    notes: list[str] = field(default_factory=list)


# ── Literal parameter extraction ──────────────────────────────────────────────

_OPTION_ID_RE = re.compile(
    r"\b(drop-dependency:[^\s,;\"']+|substitute:[^\s,;\"']+)", re.IGNORECASE
)
_VERSION_RE = re.compile(r"\b(?:plan\s+)?version\s+(?:is\s+)?(\d+)\b", re.IGNORECASE)
_DELAY_MIN_RE = re.compile(r"\b(\d+)\s*(?:min(?:ute)?s?)\b", re.IGNORECASE)
_DELAY_HOUR_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")

_EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("transport_delay", re.compile(r"\b(?:flight|train|bus|transport|travel)\s*(?:delay|delayed)\b|\bdelayed\s+(?:flight|train|leg)\b", re.IGNORECASE)),
    ("delivery_delay", re.compile(r"\bdelivery\s*delay\b|\blate\s+delivery\b|\bdelayed\s+delivery\b|\bdelay(?:ed)?\b", re.IGNORECASE)),
    ("resource_unavailable", re.compile(r"\b(?:resource|engineer|developer|team\s*member|staff|assignee)\s+(?:is\s+)?(?:unavailable|out|sick|gone|off)\b|\bunavailable\b", re.IGNORECASE)),
    ("service_outage", re.compile(r"\b(?:service\s+)?outage\b|\bis\s+down\b|\bwent\s+down\b|\bdown\b|\bfailed\b|\bfailure\b|\bcrash(?:ed)?\b", re.IGNORECASE)),
)


def _extract_delay_minutes(message: str) -> int | None:
    match = _DELAY_HOUR_RE.search(message)
    if match:
        return int(float(match.group(1)) * 60)
    match = _DELAY_MIN_RE.search(message)
    if match:
        return int(match.group(1))
    return None


def _extract_event_type(message: str) -> str | None:
    for event_type, pattern in _EVENT_PATTERNS:
        if pattern.search(message):
            return event_type
    return None


def _extract_severity(message: str) -> str | None:
    lowered = message.lower()
    for severity in ("critical", "high", "medium", "low"):
        if re.search(rf"\b{severity}\b", lowered):
            return severity
    if re.search(r"\b(total|complete|full)\b", lowered):
        return "critical"
    return None


def _extract_reason(message: str) -> str | None:
    quoted = _QUOTED_RE.search(message)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(r"\breason[:\-]\s*(.+)$", message, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("\"'")
    return None


def _mentions(message: str, *words: str) -> bool:
    return any(re.search(rf"\b{word}\b", message, re.IGNORECASE) for word in words)


# ── The parser ────────────────────────────────────────────────────────────────

def parse_intent(message: str) -> Intent:
    """Map a user message to an :class:`Intent` deterministically.

    Matching is most-specific-first so that e.g. "compare recovery options"
    never falls through to the generic recovery workflow. The parser never
    invents entity or scenario ids; that is resolved against live plan data by
    the orchestrator.
    """
    text = message.strip()
    if not text:
        return Intent(HELP, clarification="Ask me to inspect a plan, simulate a disruption, or plan a recovery.")

    params: dict[str, Any] = {}
    option_match = _OPTION_ID_RE.search(text)
    if option_match:
        params["option_id"] = option_match.group(1)
    version_match = _VERSION_RE.search(text)
    if version_match:
        params["expected_plan_version"] = int(version_match.group(1))
    delay = _extract_delay_minutes(text)
    if delay is not None:
        params["delay_minutes"] = delay
    severity = _extract_severity(text)
    if severity is not None:
        params["severity"] = severity
    reason = _extract_reason(text)
    if reason is not None:
        params["reason"] = reason
    event_type = _extract_event_type(text)
    if event_type is not None:
        params["event_type"] = event_type

    lowered = text.lower()

    # Help / capability questions
    if _mentions(text, "help") or _mentions(text, "what can you do", "how do you work", "capabilities"):
        return Intent(HELP, params)

    # Scheduling (only when Person 3's tool is registered does this do work)
    if re.search(r"\bschedul", lowered) or _mentions(text, "timeline plan", "recovery timeline"):
        return Intent(SCHEDULE, params)

    # Multi-tool recovery workflow: a disruption plus a request for analysis.
    # Checked before COMPARE so "…it's down, what is blocked and compare the
    # options" runs the full chain instead of a bare comparison.
    disruption_words = _mentions(
        text, "is down", "went down", "outage", "down", "failed", "failure",
        "unavailable", "crashed", "delayed", "delay",
    )
    recovery_words = _mentions(
        text, "blocked", "alternative", "recover", "compare", "what can",
        "proceed", "continue", "impact", "triage",
    )
    if disruption_words and recovery_words:
        return Intent(RECOVERY_WORKFLOW, params)

    # Option comparison
    if _mentions(text, "compare") and (
        "option_id" in params or _mentions(text, "option", "recovery", "alternatives")
    ):
        return Intent(COMPARE, params)

    # Blocker triage
    if _mentions(text, "blocked", "blocker", "triage", "what can", "still proceed", "still continue", "parallel work"):
        return Intent(TRIAGE, params)

    # Constraint validation (before the generic evaluate)
    if _mentions(text, "constraint", "hard rule", "graph rule", "cycle"):
        return Intent(CONSTRAINTS, params)

    # Structural evaluation of a proposal
    if "option_id" in params or _mentions(text, "evaluate", "validate this", "proposal", "is this recovery"):
        return Intent(EVALUATE, params)

    # Substitute candidates
    if _mentions(text, "substitute", "replacement service", "swap in", "stand-in"):
        return Intent(SUBSTITUTES, params)

    # Recovery alternatives
    if _mentions(text, "alternative", "bypass", "workaround", "work around", "recovery option"):
        return Intent(ALTERNATIVES, params)

    # Workload rebalancing
    if _mentions(text, "workload", "rebalance", "reassign", "staffing", "overloaded", "capacity", "swap people"):
        return Intent(WORKLOAD, params)

    # Relative delivery forecast
    if _mentions(text, "forecast", "how much delay", "how much longer", "delivery impact", "critical path"):
        return Intent(FORECAST, params)

    # Disruption simulation
    if _mentions(text, "what happens if", "simulate", "disruption", "outage", "is down", "went down", "unavailable", "delayed"):
        return Intent(SIMULATE_DISRUPTION, params)

    # Plan inspection
    if _mentions(text, "plan", "show me", "current state", "entities", "dependencies"):
        return Intent(PLAN_STATE, params)

    return Intent(HELP, clarification=(
        "I could not match that to a RippleBack capability. Try: \"show me the "
        "current plan\", \"what happens if the payment API goes down\", \"what is "
        "blocked and what can continue\", \"find recovery alternatives\", "
        "\"compare recovery options\", or \"rebalance the workload\"."
    ))
