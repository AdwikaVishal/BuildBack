"""Result interpretation for the RippleBack assistant.

Each function takes the *actual structured result* of one MCP tool and returns
a concise Markdown explanation suitable for a dashboard chat panel. The rules:

* Explain what happened, which entities are affected, and what can proceed.
* Surface the backend's own assumptions and limitations when they matter.
* Never fabricate dates, costs, risk, capacity, or schedule improvements —
  if the data is not in the tool result, it is not in the explanation.
* Always label hypothetical results as hypothetical.
"""

from __future__ import annotations

from typing import Any

_STATE_ORDER = ("blocked", "external", "at_risk", "healthy", "done")
_STATE_LABEL = {
    "blocked": "Blocked",
    "external": "External",
    "at_risk": "At risk",
    "healthy": "Healthy",
    "done": "Done",
}


def _inline_ids(ids: list[str], limit: int = 8) -> str:
    shown = ", ".join(f"`{i}`" for i in ids[:limit])
    if len(ids) > limit:
        shown += f" (+{len(ids) - limit} more)"
    return shown


def explain_plan_state(result: dict[str, Any]) -> str:
    entities = result.get("entities", [])
    by_status: dict[str, list[str]] = {}
    for entity in entities:
        by_status.setdefault(entity.get("status", "pending"), []).append(entity.get("id", "?"))
    lines = [
        f"**Plan `{result.get('plan_id')}`** (scenario `{result.get('scenario_id')}`, "
        f"version {result.get('version')}, status **{result.get('status')}**) — "
        f"{len(entities)} entities, {len(result.get('dependencies', []))} dependencies, "
        f"{len(result.get('constraints', []))} constraints. Committed disruptions: "
        f"{len(result.get('disruptions', []))}.",
        "",
    ]
    for status in ("done", "in_progress", "pending", "blocked"):
        if status in by_status:
            lines.append(f"- **{status.replace('_', ' ')}** ({len(by_status[status])}): {_inline_ids(by_status[status])}")
    deps = result.get("dependencies", [])
    if deps:
        chain = " → ".join(
            f"{d.get('prerequisite')}→{d.get('dependent')}" for d in deps[:4]
        )
        suffix = f" (+{len(deps) - 4} more)" if len(deps) > 4 else ""
        lines.append(f"- Dependencies: {chain}{suffix}")
    lines.append("")
    lines.append("_This is the committed plan state — nothing here is simulated._")
    return "\n".join(lines)


def explain_impact(result: dict[str, Any]) -> str:
    disruption = result.get("disruption", {})
    direct = result.get("directly_affected", [])
    transitive = result.get("transitively_affected", [])
    unaffected = result.get("unaffected", [])
    lines = [
        f"**Simulated disruption of `{disruption.get('entity_id')}`** "
        f"({disruption.get('severity', 'high')} severity: {disruption.get('reason', 'n/a')}).",
        "",
        f"- Directly affected ({len(direct)}): {_inline_ids([e['entity_id'] for e in direct])}",
        f"- Transitively affected ({len(transitive)}): {_inline_ids([e['entity_id'] for e in transitive])}",
        f"- Unaffected ({len(unaffected)}): {_inline_ids([e['entity_id'] for e in unaffected])}",
    ]
    for entry in direct[:2]:
        path = " → ".join(entry.get("evidence_path", []))
        if path:
            lines.append(f"- Evidence for `{entry['entity_id']}`: {path}")
    lines.append("")
    lines.append("_Hypothetical simulation only — the committed plan is unchanged._")
    return "\n".join(lines)


def explain_triage(result: dict[str, Any]) -> str:
    disruption = result.get("disruption", {})
    states = result.get("states", [])
    summary = result.get("summary", {})
    parallel = result.get("parallel_work", [])
    lines = [
        f"**Blocker triage after `{disruption.get('entity_id')}` "
        f"({disruption.get('event_type')}"
        + (f", {disruption.get('delay_minutes')} min delay" if disruption.get("delay_minutes") else "")
        + ")**",
        "",
        "States: " + ", ".join(
            f"**{summary.get(state, 0)}** {_STATE_LABEL[state].lower()}"
            for state in _STATE_ORDER if state in summary
        ),
        "",
    ]
    blocked = [s for s in states if s.get("state") == "blocked"]
    external = [s for s in states if s.get("state") == "external"]
    at_risk = [s for s in states if s.get("state") == "at_risk"]
    healthy = [s for s in states if s.get("state") == "healthy"]
    if blocked:
        lines.append("**Blocked — stop these:**")
        for entry in blocked[:6]:
            path = " → ".join(entry.get("evidence_path", []))
            lines.append(f"- `{entry['entity_id']}` — {entry.get('reason', '')}" + (f" _(path: {path})_" if path else ""))
        if len(blocked) > 6:
            lines.append(f"- (+{len(blocked) - 6} more)")
    if external:
        lines.append("")
        lines.append("**External — someone outside the team must act:**")
        for entry in external[:4]:
            lines.append(f"- `{entry['entity_id']}` — {entry.get('reason', '')}")
    if at_risk:
        lines.append("")
        lines.append("**At risk — can proceed, but watch closely:**")
        for entry in at_risk[:4]:
            lines.append(f"- `{entry['entity_id']}` — {entry.get('reason', '')}")
    if healthy:
        lines.append("")
        lines.append(f"**Healthy / unaffected ({len(healthy)}):** {_inline_ids([e['entity_id'] for e in healthy])}")
    if parallel:
        lines.append("")
        lines.append(f"**Work that can still proceed in parallel:** {_inline_ids([e['entity_id'] for e in parallel])}")
    critical = result.get("critical_path") or {}
    if critical.get("path"):
        lines.append("")
        lines.append(
            f"Critical path after the disruption: `{' → '.join(critical['path'])}` "
            f"({critical.get('length')} {critical.get('unit', 'steps')})."
        )
    change = result.get("critical_path_change") or {}
    impacted = change.get("impacted_entity_ids") or []
    if impacted:
        lines.append(f"Entities on the disrupted critical path: {_inline_ids([str(c) for c in impacted])}")
    lines.append("")
    lines.append("_Hypothetical triage — the committed plan is unchanged._")
    return "\n".join(lines)


def explain_alternatives(result: dict[str, Any]) -> str:
    options = result.get("options", [])
    disruption = result.get("disruption", {})
    if not options:
        return (
            f"No recovery alternatives found for `{disruption.get('entity_id')}`: "
            "nothing depends on it in a way that can be bypassed. Independent work "
            "is unaffected."
        )
    lines = [
        f"**{len(options)} recovery alternative(s)** for `{disruption.get('entity_id')}` "
        "— each bypasses one broken dependency:",
        "",
    ]
    for option in options:
        dep = option.get("affected_dependency", {})
        lines.append(
            f"- `{option['option_id']}` — bypass `{dep.get('dependent')}` → `{dep.get('prerequisite')}`; "
            f"unblocks: {_inline_ids(option.get('unblocks', []))}"
        )
        for assumption in option.get("assumptions", [])[:2]:
            lines.append(f"  - Assumption: {assumption}")
    lines.append("")
    lines.append(
        "_Suggestions only — nothing is committed. Structural bypass is **not** a "
        "claim the change is business-safe; review the assumptions above._"
    )
    return "\n".join(lines)


def explain_substitutes(result: dict[str, Any]) -> str:
    outcome = result.get("outcome")
    options = result.get("options", [])
    if outcome == "no_substitute_candidates":
        missing = result.get("missing_data", [])
        lines = [
            f"**No substitute candidates** for `{result.get('disruption', {}).get('entity_id')}`.",
            "",
            "RippleBack only proposes substitutes a scenario explicitly declares via "
            "`metadata.substitutes`; nothing is inferred from names or graph structure.",
        ]
        if missing:
            lines.append("")
            lines.append("Missing data:")
            lines.extend(f"- {m}" for m in missing)
        return "\n".join(lines)
    if outcome == "no_feasible_substitute":
        return (
            "**Declared substitute candidates exist, but none is feasible** — every "
            "candidate failed hard-constraint or graph validation. See the per-option "
            "violations in the structured result."
        )
    lines = [f"**{len(options)} substitute option(s) proposed:**", ""]
    for option in options:
        lines.append(
            f"- `{option['option_id']}` — replaces `{option.get('disrupted_entity_id')}` "
            f"with `{option.get('substitute_entity_id')}`; "
            f"feasible: **{option.get('feasible')}**"
        )
    lines.append("")
    lines.append("_Hypothetical — substitutes come from explicit scenario declarations only; nothing is committed._")
    return "\n".join(lines)


def explain_evaluation(result: dict[str, Any]) -> str:
    verdict = result.get("verdict")
    lines = [
        f"**Evaluation `{result.get('evaluation_id')}`**: verdict **{verdict}** "
        f"(expected plan version {result.get('expected_plan_version')}, "
        f"committed version {result.get('plan_version')}).",
        "",
    ]
    for finding in result.get("findings", []):
        marker = "⚠️" if finding.get("level") == "warning" else ("❌" if finding.get("level") == "error" else "•")
        lines.append(f"{marker} `{finding.get('code')}` — {finding.get('message')}")
    impact = result.get("impact")
    if impact:
        lines.append("")
        lines.append(f"- Would unblock: {_inline_ids(impact.get('unblocks', []))}")
        if impact.get("still_blocked"):
            lines.append(f"- Still blocked: {_inline_ids(impact.get('still_blocked'))}")
    lines.append("")
    if verdict == "structurally_valid":
        lines.append(
            "⚠️ **Structurally valid ≠ business-safe.** This verdict only confirms the "
            "edge exists, references resolve, and the version matches. Business "
            "readiness is explicitly not assessed and must be reviewed by a human."
        )
    else:
        lines.append("This proposal fails structural checks and should not be adopted as-is.")
    return "\n".join(lines)


def explain_workload(result: dict[str, Any]) -> str:
    if result.get("outcome") == "insufficient_data":
        return (
            f"**No staffing data** for scenario `{result.get('scenario_id')}` — "
            "`rebalance_workload` needs per-entity `assignee`/`effort` values and a "
            "`resources` list. I will not invent capacity numbers; pick a staffed "
            "scenario (e.g. the dashboard's staffed demo plan) to see rebalancing."
        )
    resources = result.get("resources", [])
    suggestions = result.get("suggestions", [])
    unresolved = result.get("unresolved", [])
    overloaded = [r for r in resources if r.get("overloaded")]
    lines = [
        f"**Workload rebalance** ({result.get('unit', 'work_units')}): "
        f"{len(overloaded)} overloaded resource(s) of {len(resources)}.",
        "",
    ]
    for resource in resources:
        flag = "🔴 overloaded" if resource.get("overloaded") else "ok"
        lines.append(
            f"- `{resource['resource_id']}`: {resource.get('assigned_effort')}/{resource.get('capacity')} "
            f"({flag})"
        )
    if suggestions:
        lines.append("")
        lines.append("**Suggested reassignments:**")
        for suggestion in suggestions:
            lines.append(
                f"- `{suggestion['entity_id']}` ({suggestion.get('effort')} "
                f"{result.get('unit', 'work_units')}): `{suggestion['from_resource_id']}` → "
                f"`{suggestion['to_resource_id']}`"
            )
    if unresolved:
        lines.append("")
        lines.append(f"Could not resolve ({len(unresolved)}): {_inline_ids([u['entity_id'] for u in unresolved])}")
    lines.append("")
    lines.append(
        "_Hypothetical and read-only — assignments are not changed, and no delivery "
        "date is predicted. Skills and availability are not modeled._"
    )
    return "\n".join(lines)


def explain_constraints(result: dict[str, Any]) -> str:
    feasible = result.get("feasible")
    violations = result.get("violations", [])
    lines = [
        f"**Hard-constraint validation**: feasible = **{feasible}** "
        f"({len(violations)} violation(s)).",
        "",
    ]
    for violation in violations:
        lines.append(f"- `{violation.get('code')}` ({violation.get('source')}): {violation.get('message')}")
    if feasible:
        lines.append("All graph rules passed and every constraint is supported and currently satisfied.")
    else:
        lines.append("This change set must not be adopted until the violations above are resolved.")
    return "\n".join(lines)


def explain_forecast(result: dict[str, Any]) -> str:
    if result.get("feasible") is False:
        error = result.get("error") or {}
        return (
            "**Forecast not possible**: the proposed change set would produce an "
            f"invalid graph (`{error.get('code')}`: {error.get('message')}). No "
            "forecast is reported — an invalid change set is never shown as a valid "
            "result."
        )
    unit = result.get("unit", "steps")
    lines = [
        f"**Relative delivery forecast** ({unit}):",
        "",
        f"- Baseline critical path: `{' → '.join(result.get('baseline_path', []))}` = {result.get('baseline_length')} {unit}",
    ]
    if result.get("forecast_path") is not None:
        lines.append(
            f"- Forecast critical path: `{' → '.join(result.get('forecast_path', []))}` = "
            f"{result.get('forecast_length')} {unit}"
        )
    delta = result.get("delay_delta")
    if delta is not None:
        direction = "shorter" if delta < 0 else ("longer" if delta > 0 else "unchanged")
        lines.append(f"- Delay delta: **{delta:+g} {unit}** ({direction})")
    lines.append("")
    lines.append(
        "_Relative dependency-chain comparison only — **never a calendar date**. "
        "Hypothetical; nothing is committed._"
    )
    return "\n".join(lines)


def explain_comparison(result: dict[str, Any]) -> str:
    outcome = result.get("outcome")
    recommendation = result.get("recommendation")
    ranked = result.get("ranked", [])
    excluded = result.get("excluded", [])
    baseline = result.get("baseline", {})
    lines = [
        f"**Recovery option comparison** — outcome **{outcome}**, "
        f"{len(ranked)} ranked / {len(excluded)} excluded. "
        f"Baseline critical path {baseline.get('length')} {baseline.get('unit', 'steps')}.",
        "",
    ]
    for entry in ranked[:5]:
        score = entry.get("score")
        delta = (entry.get("forecast") or {}).get("delay_delta")
        marker = "⭐ " if entry.get("option_id") == recommendation else ""
        lines.append(
            f"- {marker}`{entry['option_id']}` — score {score if score is not None else 'n/a'}"
            + (f", delay delta {delta:+g}" if isinstance(delta, (int, float)) else "")
        )
    if excluded:
        lines.append("")
        lines.append("**Excluded (never recommended):**")
        for entry in excluded[:5]:
            reasons = "; ".join(entry.get("reasons", [])[:2]) or "see violations"
            lines.append(f"- `{entry['option_id']}` — {reasons}")
    lines.append("")
    if recommendation:
        lines.append(
            f"**Recommended (structurally): `{recommendation}`** — lowest delay score "
            "among feasible, scorable options. This is a structural recommendation "
            "only; validate it with a human before adopting."
        )
    else:
        lines.append("No feasible recovery option exists for this change set.")
    return "\n".join(lines)


def explain_schedule_missing(discovered_tools: list[str]) -> str:
    return (
        "**No scheduling tool is registered on the MCP server**, so I cannot "
        "generate a recovery schedule. The server currently exposes: "
        + ", ".join(f"`{t}`" for t in discovered_tools)
        + ". When a scheduling tool is registered it will be discovered "
        "automatically and this request will route to it. I will not fabricate a "
        "schedule in the meantime."
    )


def explain_tool_error(tool: str, error: str) -> str:
    return f"`{tool}` returned an error: {error}"


#: Interpreter registry — intent/tool name → explanation function.
EXPLAINERS = {
    "get_plan_state": explain_plan_state,
    "simulate_disruption": explain_impact,
    "analyze_blocked_tasks": explain_triage,
    "find_alternative_dependencies": explain_alternatives,
    "find_substitute_options": explain_substitutes,
    "evaluate_recovery_plan": explain_evaluation,
    "rebalance_workload": explain_workload,
    "validate_hard_constraints": explain_constraints,
    "recalculate_delivery_forecast": explain_forecast,
    "compare_recovery_options": explain_comparison,
}


def explain(tool: str, result: Any) -> str:
    """Explain a structured tool result, falling back to a JSON excerpt."""
    fn = EXPLAINERS.get(tool)
    if fn is not None and isinstance(result, dict):
        try:
            return fn(result)
        except Exception:  # defensive: a malformed result must not crash chat
            pass
    import json as _json

    text = _json.dumps(result, indent=2) if not isinstance(result, str) else result
    if len(text) > 1500:
        text = text[:1500] + "… (truncated; full result in the MCP Inspector)"
    return f"```\n{text}\n```"
