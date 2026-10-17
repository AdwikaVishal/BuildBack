"""RippleBack MCP server.

Exposes six read-only tools over the Streamable HTTP transport:

* ``get_plan_state`` — committed plan state.
* ``simulate_disruption`` — hypothetical dependency-impact analysis.
* ``find_alternative_dependencies`` — hypothetical recovery alternatives.
* ``evaluate_recovery_plan`` — structural evaluation of a recovery proposal.
* ``rebalance_workload`` — hypothetical reassignment suggestions (staffed plans).
* ``analyze_blocked_tasks`` — blocker triage: one state per entity plus the work
  that can still proceed (BuildBack).

No writes yet.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from rippleback.evaluation import (
    EvaluationError,
    evaluate_recovery_plan as _evaluate,
)
from rippleback.graph import GraphError
from rippleback.impact import ImpactError, simulate_disruption as _simulate
from rippleback.recovery import (
    RecoveryError,
    find_alternative_dependencies as _find_alternatives,
)
from rippleback.scenarios import ScenarioError, get_scenario
from rippleback.triage import TriageError
from rippleback.triage import analyze_blocked_tasks as _analyze_blocked
from rippleback.workload import WorkloadError, rebalance_workload as _rebalance

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

mcp = MCPServer(
    name="RippleBack",
    version="0.1.0",
    instructions=(
        "RippleBack project-disruption MCP server. "
        "Exposes plan-state inspection, hypothetical disruption impact, "
        "hypothetical recovery alternatives, structural evaluation of a "
        "recovery proposal, hypothetical workload rebalancing for staffed "
        "plans, and blocker triage (which work is blocked, external, at risk, "
        "healthy, or done, and what can still proceed)."
    ),
)


@mcp.tool(
    name="get_plan_state",
    description=(
        "Return the current state of a seeded project scenario by scenario id: "
        "plan id, version, health status, entities, directed dependencies, and "
        "constraints. Unknown scenario ids raise a structured error. "
        "No impact analysis or recovery options in this phase."
    ),
)
def get_plan_state(scenario_id: str) -> dict[str, Any]:
    """Return structured plan state for the given scenario id.

    Read-only. Raises ``ToolError`` for unknown or malformed scenarios so the
    client receives a clear, structured error instead of a generic crash.
    """
    try:
        scenario = get_scenario(scenario_id)
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
        "disruptions": [],
    }


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
        scenario = get_scenario(scenario_id)
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
        scenario = get_scenario(scenario_id)
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
        scenario = get_scenario(scenario_id)
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
        scenario = get_scenario(scenario_id)
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
        scenario = get_scenario(scenario_id)
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
    """Entry point: start the MCP server over Streamable HTTP."""
    host = os.environ.get("RIPPLEBACK_HOST", DEFAULT_HOST)
    port = _resolve_port()
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
