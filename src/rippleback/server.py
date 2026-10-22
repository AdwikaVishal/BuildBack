"""RippleBack MCP server.

Exposes fourteen read-only analysis tools plus five gated write tools over the
Streamable HTTP transport:

Read-only
~~~~~~~~~
* ``get_plan_state`` — committed plan state (with real open disruptions).
* ``list_scenarios`` — every known scenario (seeded and PM-synced).
* ``simulate_disruption`` — hypothetical dependency-impact analysis.
* ``find_alternative_dependencies`` — hypothetical recovery alternatives.
* ``evaluate_recovery_plan`` — structural evaluation of a recovery proposal.
* ``rebalance_workload`` — hypothetical reassignment suggestions (staffed plans).
* ``analyze_blocked_tasks`` — blocker triage: one state per entity plus the work
  that can still proceed (BuildBack).
* ``recalculate_delivery_forecast`` — relative critical-path forecast.
* ``validate_hard_constraints`` — hard-constraint and graph validation.
* ``compare_recovery_options`` — feasibility gate, scoring, ranking, forecasts,
  with cost/risk/disruption inputs derived from configured sources.
* ``find_substitute_options`` — explicit substitute candidates.
* ``generate_recovery_schedule`` — relative-duration recovery schedule.

Gated writes (all require ``RIPPLEBACK_WRITES_ENABLED=true``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* ``dry_run_recovery_plan`` — validate + diff, returns a confirm token.
* ``record_disruption`` — commit a real disruption (blocks the entity).
* ``resolve_disruption`` — resolve it and restore the recorded status.
* ``commit_recovery_plan`` — commit a confirmed recovery.
* ``commit_reassignment`` — commit a confirmed reassignment.

Nothing mutates committed state except the write tools, each of which is
guarded, audited, and idempotency-keyed. The chat assistant cannot reach the
write tools (see :mod:`rippleback.assistant.orchestrator`).
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver import Context

from rippleback import repository
from rippleback.commit import (
    CommitError,
    commit_reassignment as _commit_reassignment,
    commit_recovery as _commit_recovery,
    dry_run_commit as _dry_run,
    record_disruption as _record_disruption,
    resolve_disruption as _resolve_disruption,
)
from rippleback.security import write_guard_reason
from rippleback.comparison import compare_recovery_options as _compare
from rippleback.constraints import (
    ConstraintError,
    validate_hard_constraints as _validate,
)
from rippleback.evaluation import (
    EvaluationError,
    evaluate_recovery_plan as _evaluate,
)
from rippleback.forecast import (
    ForecastError,
    recalculate_delivery_forecast as _forecast,
)
from rippleback.graph import GraphError
from rippleback.impact import ImpactError, simulate_disruption as _simulate
from rippleback import metrics as _metrics
from rippleback.recovery import (
    RecoveryError,
    find_alternative_dependencies as _find_alternatives,
    find_substitute_options as _find_substitutes,
)
from rippleback.scheduler import (
    SchedulerError,
    generate_recovery_schedule as _schedule,
)
from rippleback.scenarios import ScenarioError
from rippleback.scoring import ScoringError
from rippleback.triage import TriageError
from rippleback.triage import analyze_blocked_tasks as _analyze_blocked
from rippleback.workload import WorkloadError, rebalance_workload as _rebalance

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

_SUBSTITUTE_ID_PREFIX = "substitute:"
_SUBSTITUTE_ID_HINT = (
    "uses the substitute id format, which the legacy "
    "'drop-dependency:' option-id parser does not support; pass that "
    "option's explicit 'changes' list instead."
)

mcp = MCPServer(
    name="RippleBack",
    version="0.1.0",
    instructions=(
        "RippleBack project-disruption MCP server. "
        "Exposes plan-state inspection, scenario listing, hypothetical disruption "
        "impact, hypothetical recovery alternatives, structural evaluation of a "
        "recovery proposal, hypothetical workload rebalancing for staffed "
        "plans, blocker triage, relative critical-path delivery forecasts for a "
        "proposed change set, hard-constraint and graph validation of a proposed "
        "change set, comparison and ranking of recovery options with per-option "
        "forecasts and configured cost/risk/disruption inputs, explicit "
        "substitute-candidate discovery (declared via entity metadata only), and "
        "relative-duration recovery scheduling (never calendar dates). Analysis "
        "tools are read-only and hypothetical. Five write tools (dry run, record "
        "disruption, resolve disruption, commit recovery, commit reassignment) "
        "are gated behind RIPPLEBACK_WRITES_ENABLED and are audited and "
        "idempotency-keyed; nothing is committed unless writes are enabled and "
        "the caller supplies a confirm token from a dry run."
    ),
)


@mcp.tool(
    name="get_plan_state",
    description=(
        "Return the current state of a scenario by scenario id: plan id, "
        "version, health status, entities, directed dependencies, constraints, "
        "and any open (recorded) disruptions. Seeded demo scenarios and plans "
        "synced from a PM provider are both available. Unknown scenario ids "
        "raise a structured error."
    ),
)
def get_plan_state(scenario_id: str) -> dict[str, Any]:
    """Return structured plan state for the given scenario id.

    Read-only. Raises ``ToolError`` for unknown or malformed scenarios so the
    client receives a clear, structured error instead of a generic crash.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        disruptions = repository.list_open_disruptions(scenario["scenario_id"])
    except ScenarioError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "scenario_id": scenario["scenario_id"],
        "plan_id": scenario["plan_id"],
        "version": scenario["version"],
        "status": scenario["status"],
        "entities": scenario["entities"],
        "dependencies": scenario["dependencies"],
        "constraints": scenario["constraints"],
        "disruptions": disruptions,
    }


@mcp.tool(
    name="list_scenarios",
    description=(
        "List every scenario this server knows about: the seeded demo "
        "scenarios and any plan materialized from a PM sync. Each entry reports "
        "its scenario id, plan id, source (seeded|jira|linear|github|sample), "
        "current version, health status, entity count, open-disruption count, "
        "and last sync time. Read-only."
    ),
)
def list_scenarios() -> dict[str, Any]:
    """Return light metadata for every known scenario. Read-only."""
    return repository.list_scenarios()


@mcp.tool(
    name="simulate_disruption",
    description=(
        "Hypothetically disrupt one entity in a seeded scenario and return the "
        "dependency impact: directly affected, transitively affected, and "
        "unaffected entities, each with an evidence path. This is a simulation "
        "only — the committed plan is not modified and its version is unchanged. "
        "Unknown scenario/entity ids and invalid details return a structured error."
    ),
)
def simulate_disruption(
    scenario_id: str,
    entity_id: str,
    reason: str,
    severity: str = "high",
) -> dict[str, Any]:
    """Simulate a disruption and return a hypothetical impact report.

    Read-only. Raises ``ToolError`` for unknown scenarios/entities or invalid
    disruption details so the client receives a clear, structured error.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        return _simulate(
            scenario, entity_id=entity_id, reason=reason, severity=severity
        )
    except (ScenarioError, ImpactError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="find_alternative_dependencies",
    description=(
        "Given a disrupted entity in a seeded scenario, return deterministic "
        "hypothetical recovery alternatives: for each dependency that requires "
        "the disrupted entity, an option proposing to bypass that dependency, "
        "with a stable id, description, affected dependency, assumptions, and "
        "limitations. Suggestions only — the committed plan is not modified. "
        "Unknown scenario/entity ids return a structured error."
    ),
)
def find_alternative_dependencies(
    scenario_id: str,
    entity_id: str,
) -> dict[str, Any]:
    """Return hypothetical alternative-dependency options for a disrupted entity.

    Read-only. Raises ``ToolError`` for unknown scenarios/entities so the client
    receives a clear, structured error instead of a generic crash.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        return _find_alternatives(scenario, entity_id=entity_id)
    except (ScenarioError, RecoveryError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="evaluate_recovery_plan",
    description=(
        "Structurally evaluate a recovery proposal against a seeded scenario. "
        "The proposal is a recovery option id (see find_alternative_dependencies) "
        "plus the plan version it was built against. Checks that the entity "
        "references resolve, the dependency edge exists, and the expected plan "
        "version matches the committed version, then reports remaining "
        "prerequisites and downstream effects using the residual graph. The "
        "verdict is structural only — business readiness is explicitly not "
        "assessed. Read-only: the committed plan is never modified. Unknown "
        "scenario ids and malformed proposals return a structured error; "
        "well-formed proposals that fail a check return a 'rejected' report."
    ),
)
def evaluate_recovery_plan(
    scenario_id: str,
    option_id: str,
    expected_plan_version: int,
) -> dict[str, Any]:
    """Return a deterministic, read-only evaluation of a recovery proposal.

    Read-only. Raises ``ToolError`` for unknown scenarios and malformed
    proposals (bad option id or non-integer version) so the client receives a
    clear, structured error. Proposals that fail structural checks (unknown
    entity, missing edge, stale version) return a ``rejected`` report.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        return _evaluate(
            scenario,
            option_id=option_id,
            expected_plan_version=expected_plan_version,
        )
    except (ScenarioError, EvaluationError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="rebalance_workload",
    description=(
        "Propose hypothetical workload reassignments for a staffed scenario. "
        "Given a scenario with a resources list and per-entity assignee/effort "
        "values, report each resource's capacity, unfinished assigned effort, "
        "free capacity, and overload status, then suggest deterministic moves "
        "that relieve overloaded resources, with stable suggestion ids. "
        "Effort is in abstract 'work_units' with no time dimension. Read-only: "
        "committed assignments, dependencies, statuses, and version are never "
        "changed; no delivery dates are predicted. Requires "
        "expected_plan_version (stale versions are rejected). Optionally scope "
        "the analysis to one resource with resource_id. Scenarios without "
        "staffing data return an explicit 'insufficient_data' outcome. Unknown "
        "scenario/resource ids return a structured error."
    ),
)
def rebalance_workload(
    scenario_id: str,
    expected_plan_version: int,
    resource_id: str | None = None,
) -> dict[str, Any]:
    """Return deterministic, read-only reassignment suggestions.

    Read-only. Raises ``ToolError`` for unknown scenarios/resources and stale
    or invalid plan versions so the client receives a clear, structured error.
    Scenarios without staffing data return an explicit ``insufficient_data``
    outcome rather than a silent empty rebalance.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        return _rebalance(
            scenario,
            expected_plan_version=expected_plan_version,
            resource_id=resource_id,
        )
    except (ScenarioError, WorkloadError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="analyze_blocked_tasks",
    description=(
        "Blocker triage (BuildBack): after a disruption, assign exactly one "
        "state to every entity — blocked, external, at_risk, healthy, or done — "
        "with a reason and an evidence path, and list the parallel work that can "
        "still proceed. Hard requires_* paths block; benefits_from edges only add "
        "risk; time delays are absorbed by entity slack so downstream work is not "
        "assumed to fail. Also reports the critical path and how the disruption "
        "changes it. Read-only: the committed plan is never modified. Unknown "
        "scenario/entity ids and invalid events return a structured error."
    ),
)
def analyze_blocked_tasks(
    scenario_id: str,
    entity_id: str,
    event_type: str = "service_outage",
    reason: str | None = None,
    delay_minutes: float | None = None,
) -> dict[str, Any]:
    """Return one triage state per entity plus the work that can still proceed.

    Read-only. Raises ``ToolError`` for unknown scenarios/entities, unknown event
    types, missing delay parameters, and malformed graphs so the client receives
    a clear, structured error.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        parameters = None if delay_minutes is None else {"delay_minutes": delay_minutes}
        return _analyze_blocked(
            scenario,
            entity_id=entity_id,
            event_type=event_type,
            reason=reason,
            parameters=parameters,
        )
    except (ScenarioError, TriageError, GraphError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="recalculate_delivery_forecast",
    description=(
        "Compare a scenario's committed critical path with the critical path "
        "after applying a proposed change set (or a drop-dependency option id), "
        "and report the delay_delta in relative duration units (steps or "
        "duration_units) — never calendar dates. The change set uses the "
        "apply_changes shape: remove_dependency/add_dependency entries. "
        "Substitute option ids (substitute:...) are rejected with guidance to "
        "pass the option's explicit changes, because only the legacy "
        "drop-dependency id format is parseable here. Invalid inputs (unknown "
        "scenario/entity, malformed changes or option id, both changes and "
        "option_id) return a structured error; a well-formed change set that "
        "produces an invalid graph (e.g. a cycle) is reported in-band with "
        "feasible: false and null forecast fields, never as a valid forecast. "
        "Read-only: the committed plan is never modified."
    ),
)
def recalculate_delivery_forecast(
    scenario_id: str,
    changes: list[dict[str, Any]] | None = None,
    option_id: str | None = None,
    disrupted_entity_id: str | None = None,
) -> dict[str, Any]:
    """Return a relative critical-path baseline-vs-forecast report.

    Read-only. Raises ``ToolError`` for unknown scenarios/entities, malformed
    changes or option ids, conflicting inputs, and substitute-format option ids
    (which must be passed as explicit ``changes``). Invalid graphs come back
    in-band with ``feasible: False``.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        if (
            isinstance(option_id, str)
            and option_id.startswith(_SUBSTITUTE_ID_PREFIX)
        ):
            raise ToolError(f"option_id {option_id!r} {_SUBSTITUTE_ID_HINT}")
        return _forecast(
            scenario,
            changes=changes,
            option_id=option_id,
            disrupted_entity_id=disrupted_entity_id,
        )
    except (ScenarioError, ForecastError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="validate_hard_constraints",
    description=(
        "Validate a scenario's hard constraints and dependency-graph rules for "
        "a proposed change set (default: the committed plan itself, baseline "
        "validation). The change set uses the apply_changes shape. Returns "
        "feasible plus machine-readable violations: graph faults first "
        "(ENTITY_NOT_FOUND, INVALID_DEPENDENCY, INVALID_DEPENDENCY_TYPE, "
        "CYCLIC_DEPENDENCY) then committed constraints in fixture order "
        "(currently all_entities_done_before, evaluated as a current-state "
        "invariant over committed statuses; unsupported types are violations, "
        "never silently skipped). 'feasible: true' means every graph rule "
        "passed and every constraint is supported and currently satisfied. "
        "Invalid requests (unknown scenario, malformed scenario, non-list "
        "changes, unsupported change action) return a structured error. "
        "Read-only: statuses and the committed plan are never modified, and "
        "no scoring or scheduling happens here."
    ),
)
def validate_hard_constraints(
    scenario_id: str,
    changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a deterministic hard-constraint and graph validation report.

    Read-only. Raises ``ToolError`` for unknown scenarios and malformed
    change sets; well-formed but invalid proposals return ``feasible: False``
    with their violations in-band.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        return _validate(scenario, changes=changes)
    except (ScenarioError, ConstraintError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="compare_recovery_options",
    description=(
        "Feasibility-gate, score, rank, and attach delivery forecasts to a "
        "list of recovery option objects (each needs a unique option_id; "
        "either an explicit changes list — including substitute: options — or "
        "a legacy drop-dependency id derived into a remove_dependency change). "
        "Feasibility is gated with hard-constraint/graph validation first: "
        "infeasible options are excluded with their violations and are never "
        "scored or recommended. Score = w_delay*delay + w_cost*cost + w_risk*risk "
        "+ w_strain*resource_strain + w_count*disruption_count, lower is "
        "better, ties break by ascending option_id. delay comes from the "
        "relative critical-path forecast; resource_strain from workload overload "
        "when staffing data exists. cost, risk, and disruption_count are "
        "derived from configured sources (COST_RATE_PER_WORK_UNIT or a PM cost "
        "field; incident history and open disruptions) using the documented "
        "formulas — never fabricated. A metric keeps weight 0.0 unless its "
        "source is configured AND every option yields a value, so a pristine "
        "demo is never accidentally unscorable. SCORE_WEIGHT_* env vars force "
        "any weight. The report echoes resolved 'weights' and 'metric_sources' "
        "(default|env|data|caller|disabled_*). recommendation is the top "
        "feasible, scorable option, or null with outcome "
        "no_feasible_recovery. Every entry carries its own forecast report. "
        "Weights, metric keys, and option lists are validated; malformed "
        "input returns a structured error. Read-only: nothing is committed."
    ),
)
def compare_recovery_options(
    scenario_id: str,
    options: list[dict[str, Any]],
    weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the gated, scored, ranked comparison report with forecasts.

    Read-only. Raises ``ToolError`` for unknown scenarios, malformed option
    lists/ids/changes, and malformed weights or metrics; infeasible options
    are an in-band ``excluded`` result, never a recommendation.

    Cost, risk, and disruption_count inputs are derived from configured
    sources (``COST_RATE_PER_WORK_UNIT``/PM cost fields, incident history and
    open disruptions) and merged under any caller-supplied ``metrics``; the
    effective weights and a ``metric_sources`` label per metric are echoed back.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        if not isinstance(options, list) or not options:
            raise ToolError("options must be a non-empty list")
        derived = _metrics.derive_metrics(scenario, options)
        enriched: list[Any] = []
        for option in options:
            if not isinstance(option, dict):
                enriched.append(option)
                continue
            entry = dict(option)
            values = derived.get(entry.get("option_id"))
            if values:
                entry["metrics"] = {**values, **(entry.get("metrics") or {})}
            enriched.append(entry)
        if weights is None:
            effective_weights, sources = _metrics.resolve_weights(scenario, options)
        else:
            effective_weights = weights
            sources = {
                metric: ("caller" if metric in (weights or {}) else "default")
                for metric in _metrics.METRIC_KEYS
            }
        report = _compare(scenario, enriched, effective_weights)
        report["metric_sources"] = sources
        return report
    except (ScenarioError, ConstraintError, ScoringError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="find_substitute_options",
    description=(
        "Propose substitute-service recovery options for a disrupted "
        "prerequisite. A candidate is proposed only when the entity explicitly "
        "declares 'metadata.substitutes' — a list of entity ids in the same "
        "scenario — or when the substitute catalog has entries for that entity "
        "(imported via 'rippleback seed-sample', SUBSTITUTES_FILE, or "
        "POST /api/substitutes/import). Absent or empty declarations return "
        "outcome 'no_substitute_candidates' with missing_data explaining what is "
        "missing (no seeded fixture declares any). Nothing is inferred from "
        "names, statuses, resources, or graph structure, and no service, "
        "availability, compatibility, cost, or risk is invented. Each declared "
        "candidate becomes one substitute:{prerequisite}->{substitute} option "
        "whose change set reroutes every dependency edge on the disrupted "
        "entity (one remove_dependency plus one add_dependency per dependent, "
        "preserving dependency_type and constraint_parameters), validated "
        "against hard constraints and graph rules; failures come back as "
        "feasible: false with reasons and stable violation codes. Outcome is "
        "one of substitutes_proposed, no_feasible_substitute, or "
        "no_substitute_candidates. Options carry explicit changes, so they "
        "flow into compare_recovery_options and recalculate_delivery_forecast "
        "unchanged (the legacy drop-dependency id parser does not accept "
        "substitute ids). Unknown scenario/entity ids and malformed "
        "substitutes declarations return a structured error. Read-only: "
        "nothing is committed."
    ),
)
def find_substitute_options(
    scenario_id: str,
    entity_id: str,
) -> dict[str, Any]:
    """Return explicit substitute-candidate options for a disrupted entity.

    Read-only. Raises ``ToolError`` for unknown scenarios/entities and for a
    malformed ``metadata.substitutes`` declaration; fixtures without declared
    candidates return ``outcome: "no_substitute_candidates"``.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        return _find_substitutes(scenario, entity_id=entity_id)
    except (ScenarioError, RecoveryError, ConstraintError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="generate_recovery_schedule",
    description=(
        "Generate a hypothetical, read-only schedule for a scenario, optionally "
        "after a proposed recovery change set (the apply_changes shape) or a "
        "legacy drop-dependency option id, and optionally with a disrupted "
        "entity whose readiness is recomputed with blocker triage against the "
        "modified graph. Reports per-task readiness groups (completed, "
        "ready_now, waiting, blocked), parallel execution layers, a "
        "deterministic execution order, hard/soft prerequisites, critical-path "
        "membership, and — only when every task declares a duration — earliest "
        "start/finish times, waiting vs execution time, and a projected "
        "completion duration in relative units. Missing durations are never "
        "invented: the report comes back as outcome insufficient_duration_data "
        "with ordering only and missing_data explaining what is absent. With a "
        "change set, the report also includes the committed plan's baseline "
        "schedule and a baseline-vs-recovery comparison (execution orders, "
        "critical paths, completion delta only when comparable, per-task "
        "offset changes, newly blocked/unblocked tasks, parallelism changes). "
        "Proposals that create a cycle, a dangling reference, or a hard-"
        "constraint violation are reported in-band as outcome infeasible with "
        "stable violation codes and no schedule — an infeasible proposal is "
        "never ordered or recommended. scheduling_config may declare "
        "duration_unit (a label; 'minutes' additionally honors "
        "required_buffer_minutes arrival buffers) and durations (explicit "
        "caller-supplied per-entity durations). Relative time only: no "
        "calendar, start date, or working hours are modeled, and no resource "
        "constraints are applied. Unknown scenario/entity ids and malformed "
        "inputs return a structured error. Read-only: the committed plan is "
        "never modified."
    ),
)
def generate_recovery_schedule(
    scenario_id: str,
    changes: list[dict[str, Any]] | None = None,
    option_id: str | None = None,
    disrupted_entity_id: str | None = None,
    scheduling_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a hypothetical, read-only relative-duration schedule report.

    Read-only. Raises ``ToolError`` for unknown scenarios/entities, malformed
    change sets or option ids, conflicting inputs, substitute-format option
    ids (which must be passed as explicit ``changes``), and invalid
    scheduling configs. Invalid graphs and constraint violations come back
    in-band with ``feasible: False`` and ``outcome: "infeasible"``.
    """
    try:
        scenario = repository.get_scenario(scenario_id)
        if (
            isinstance(option_id, str)
            and option_id.startswith(_SUBSTITUTE_ID_PREFIX)
        ):
            raise ToolError(f"option_id {option_id!r} {_SUBSTITUTE_ID_HINT}")
        return _schedule(
            scenario,
            changes=changes,
            option_id=option_id,
            disrupted_entity_id=disrupted_entity_id,
            scheduling_config=scheduling_config,
        )
    except (ScenarioError, SchedulerError, ConstraintError, GraphError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="dry_run_recovery_plan",
    description=(
        "Validate a proposed recovery or reassignment against the committed "
        "plan and return a deterministic diff plus a short-lived confirm token "
        "bound to this exact scenario, expected version, and change set. The "
        "plan is never modified. Requires RIPPLEBACK_WRITES_ENABLED=true "
        "(a dry run is refused otherwise, since no write could follow it). "
        "Pass either changes or option_id (drop-dependency ids only; "
        "substitute ids must be passed as explicit changes). Rejects a stale "
        "expected_version and any change set that would violate hard "
        "constraints or graph rules, reporting the violation codes. The token "
        "expires after RIPPLEBACK_CONFIRM_TOKEN_TTL_SECONDS (default 300)."
    ),
)
def dry_run_recovery_plan(
    scenario_id: str,
    expected_version: int,
    option_id: str | None = None,
    changes: list[dict[str, Any]] | None = None,
    kind: str = "recovery",
    ctx: Context = None,
) -> dict[str, Any]:
    """Return a dry-run report: diff, changes, and a confirm token.

    Write-gated (refused when writes are disabled) but never mutates state.
    Raises ``ToolError`` for unknown scenarios, stale versions, infeasible
    change sets, and malformed inputs.
    """
    headers = None
    try:
        if ctx is not None and hasattr(ctx, "request_context") and ctx.request_context:
            req = getattr(ctx.request_context, "request", None)
            if req is not None and hasattr(req, "headers"):
                headers = req.headers
    except Exception:
        headers = None
    reason = write_guard_reason("dry_run_recovery_plan", is_write=True, headers=headers)
    if reason is not None:
        raise ToolError(reason)
    try:
        return _dry_run(
            scenario_id,
            expected_version=expected_version,
            changes=changes,
            option_id=option_id,
            kind=kind,
        )
    except (ScenarioError, CommitError, ConstraintError, EvaluationError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="record_disruption",
    description=(
        "Commit a real disruption: record it, advance the plan version, set the "
        "plan status to 'disrupted', and set the entity to 'blocked' — unless "
        "the entity is already 'done', which is never re-blocked (documented "
        "rule; the prior statuses are stored and restored by "
        "resolve_disruption). Requires RIPPLEBACK_WRITES_ENABLED=true and a "
        "non-empty idempotency_key: replaying the same key with the same "
        "request returns the stored result and writes nothing. Unknown "
        "scenario/entity ids, invalid event types (service_outage, "
        "resource_unavailable, delivery_delay, transport_delay — delay events "
        "require delay_minutes), and invalid severities (low, medium, high, "
        "critical) return a structured error. Returns the disruption id, the "
        "new plan version, and the before/after entity status."
    ),
)
def record_disruption(
    scenario_id: str,
    entity_id: str,
    event_type: str = "service_outage",
    reason: str | None = None,
    severity: str = "high",
    delay_minutes: float | None = None,
    idempotency_key: str = "",
    actor: str | None = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Record a real, committed disruption. Raises ``ToolError`` on refusal."""
    headers = None
    try:
        if ctx is not None and hasattr(ctx, "request_context") and ctx.request_context:
            req = getattr(ctx.request_context, "request", None)
            if req is not None and hasattr(req, "headers"):
                headers = req.headers
    except Exception:
        headers = None
    reason_guard = write_guard_reason("record_disruption", is_write=True, headers=headers)
    if reason_guard is not None:
        raise ToolError(reason_guard)
    try:
        return _record_disruption(
            scenario_id,
            entity_id=entity_id,
            event_type=event_type,
            reason=reason,
            severity=severity,
            delay_minutes=delay_minutes,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except (ScenarioError, CommitError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="resolve_disruption",
    description=(
        "Resolve a recorded disruption by id: mark it resolved, restore the "
        "entity's pre-disruption status (if it is still 'blocked'), restore the "
        "plan's prior status when no open disruptions remain, and advance the "
        "plan version. Requires RIPPLEBACK_WRITES_ENABLED=true. Resolving an "
        "already-resolved disruption is an idempotent no-op that returns "
        "replayed: true. Unknown ids return a structured error."
    ),
)
def resolve_disruption(
    disruption_id: int,
    idempotency_key: str = "",
    actor: str | None = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Resolve a recorded disruption. Raises ``ToolError`` on refusal."""
    headers = None
    try:
        if ctx is not None and hasattr(ctx, "request_context") and ctx.request_context:
            req = getattr(ctx.request_context, "request", None)
            if req is not None and hasattr(req, "headers"):
                headers = req.headers
    except Exception:
        headers = None
    reason_guard = write_guard_reason("resolve_disruption", is_write=True, headers=headers)
    if reason_guard is not None:
        raise ToolError(reason_guard)
    try:
        return _resolve_disruption(
            disruption_id, idempotency_key=idempotency_key, actor=actor
        )
    except CommitError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="commit_recovery_plan",
    description=(
        "Commit a confirmed recovery change set (remove_dependency / "
        "add_dependency) to the plan. Four gates, in order: "
        "RIPPLEBACK_WRITES_ENABLED=true; a non-empty idempotency_key (replay "
        "of the same request returns the stored result without writing; reuse "
        "with a different request is rejected); expected_version equal to the "
        "committed version (stale versions are rejected); a valid, unexpired "
        "confirm_token from dry_run_recovery_plan bound to this exact scenario, "
        "version, and change set. The change set is then re-validated against "
        "hard constraints and graph rules — an infeasible change is refused "
        "even with a valid token. Writes the new plan version, the commit row, "
        "the audit row, and (when PM write-back is enabled) the outbox row in a "
        "single transaction; PM write-back is asynchronous via the outbox "
        "worker. Returns the commit id, versions, status "
        "(local_only|writeback_pending), and the applied diff."
    ),
)
def commit_recovery_plan(
    scenario_id: str,
    expected_version: int,
    confirm_token: str,
    option_id: str | None = None,
    changes: list[dict[str, Any]] | None = None,
    idempotency_key: str = "",
    actor: str | None = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Commit a confirmed recovery. Raises ``ToolError`` when refused."""
    headers = None
    try:
        if ctx is not None and hasattr(ctx, "request_context") and ctx.request_context:
            req = getattr(ctx.request_context, "request", None)
            if req is not None and hasattr(req, "headers"):
                headers = req.headers
    except Exception:
        headers = None
    reason_guard = write_guard_reason("commit_recovery_plan", is_write=True, headers=headers)
    if reason_guard is not None:
        raise ToolError(reason_guard)
    try:
        return _commit_recovery(
            scenario_id,
            expected_version=expected_version,
            confirm_token=confirm_token,
            changes=changes,
            option_id=option_id,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except (ScenarioError, CommitError, ConstraintError, EvaluationError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="commit_reassignment",
    description=(
        "Commit a confirmed reassignment: change entity assignees "
        "([{'action': 'reassign', 'entity_id': ..., 'resource_id': ...}]) and "
        "advance the plan version. Same four gates as commit_recovery_plan "
        "(writes enabled, idempotency_key, expected_version, confirm_token from "
        "dry_run_recovery_plan with kind='reassignment'). Requires a staffed "
        "plan (a resources list); unknown entities/resources and no-op "
        "assignments are rejected. The reassignment is re-validated against "
        "resources before committing and written in one transaction with the "
        "commit, audit, and (if enabled) outbox rows."
    ),
)
def commit_reassignment(
    scenario_id: str,
    expected_version: int,
    confirm_token: str,
    changes: list[dict[str, Any]],
    idempotency_key: str = "",
    actor: str | None = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Commit a confirmed reassignment. Raises ``ToolError`` when refused."""
    headers = None
    try:
        if ctx is not None and hasattr(ctx, "request_context") and ctx.request_context:
            req = getattr(ctx.request_context, "request", None)
            if req is not None and hasattr(req, "headers"):
                headers = req.headers
    except Exception:
        headers = None
    reason_guard = write_guard_reason("commit_reassignment", is_write=True, headers=headers)
    if reason_guard is not None:
        raise ToolError(reason_guard)
    try:
        return _commit_reassignment(
            scenario_id,
            expected_version=expected_version,
            confirm_token=confirm_token,
            changes=changes,
            idempotency_key=idempotency_key,
            actor=actor,
        )
    except (ScenarioError, CommitError) as exc:
        raise ToolError(str(exc)) from exc


def _resolve_port() -> int:
    """Read RIPPLEBACK_PORT, failing with a clear message on bad input."""
    raw = os.environ.get("RIPPLEBACK_PORT", str(DEFAULT_PORT))
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(
            f"Invalid RIPPLEBACK_PORT={raw!r}: must be an integer."
        ) from exc


def main() -> None:
    """Entry point: initialize the database, then start over Streamable HTTP."""
    host = os.environ.get("RIPPLEBACK_HOST", DEFAULT_HOST)
    port = _resolve_port()
    try:
        from rippleback.db import init_db

        init_db()
    except Exception as exc:  # noqa: BLE001 - fail loudly, never leak secrets
        from rippleback.security import redact

        raise SystemExit(f"database initialization failed: {redact(str(exc))}") from exc
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
