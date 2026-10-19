# RippleBack — MCP server, impact engine, recovery alternatives, proposal evaluation, workload rebalancing, blocker triage, MCP client + conversational assistant

## 1. Purpose and scope

RippleBack is an AI-powered **project disruption and recovery platform**: it
models a project plan as entities and dependencies, detects disruptions, and
eventually plans recovery.

Implemented so far:

- One MCP server (`RippleBack`) using the official Python MCP SDK.
- Streamable HTTP transport on localhost.
- Read-only tool `get_plan_state` — committed plan state.
- Read-only tool `simulate_disruption` — deterministic, hypothetical
  dependency-impact analysis (directly / transitively affected / unaffected
  entities, each with an evidence path).
- Read-only tool `find_alternative_dependencies` — deterministic, hypothetical
  recovery alternatives (bypass a broken dependency, with assumptions and
  limitations).
- Read-only tool `evaluate_recovery_plan` — deterministic, structural evaluation
  of a recovery proposal (verdict, findings, residual-graph impact). Structural
  only — business readiness is explicitly not assessed.
- Read-only tool `rebalance_workload` — deterministic, hypothetical staffing
  rebalancing for a **staffed** scenario (per-resource capacity, unfinished
  effort, overload, and suggested reassignments in abstract `work_units`). No
  time dimension; no delivery dates are predicted.
- Read-only tool `analyze_blocked_tasks` — **blocker triage (BuildBack)**: after a
  disruption, one state per entity (`blocked`, `external`, `at_risk`, `healthy`,
  `done`), a reason and evidence path for each, the parallel work that can still
  proceed, and the critical path before/after the disruption.
- Read-only tool `recalculate_delivery_forecast` — relative critical-path
  forecast for a proposed change set (steps or `duration_units`, **never
  calendar dates**), with an in-band `feasible: false` for change sets that
  would create an invalid graph.
- Read-only tool `validate_hard_constraints` — hard-constraint and
  dependency-graph validation of a proposed change set (or the committed plan
  as baseline), with machine-readable violations.
- Read-only tool `compare_recovery_options` — feasibility gate, scoring,
  ranking, and per-option forecasts for recovery options; infeasible options
  are excluded and never recommended. Default weights score **delay only** —
  no cost or risk data exists in the repository and none is fabricated.
- Read-only tool `find_substitute_options` — substitute candidates that a
  scenario **explicitly declares** via `metadata.substitutes`. No shipped
  fixture declares any, so fixtures honestly return
  `no_substitute_candidates`.
- Read-only tool `generate_recovery_schedule` — **relative-duration recovery
  scheduling**: per-task readiness groups (`completed`, `ready_now`,
  `waiting`, `blocked`), parallel execution layers, a deterministic execution
  order, and — only when every task declares a duration — earliest
  start/finish times, waiting vs. execution time, and a projected completion
  duration in relative units (never calendar dates). Missing durations are
  never invented: the report is ordering-only with `missing_data` explaining
  what is absent. With a change set it also reports the committed plan's
  baseline schedule and a baseline-vs-recovery comparison (completion delta
  only when comparable); infeasible proposals (cycle, dangling reference,
  constraint violation) come back `feasible: false` with violations and **no
  schedule**.
- Recovery engines (`rippleback.forecast`, `rippleback.constraints`,
  `rippleback.scoring`, `rippleback.comparison`) powering those tools, plus the
  substitute-candidate engine in `rippleback.recovery` and the
  relative-duration scheduler in `rippleback.scheduler`.
- A typed dependency graph builder (`rippleback.graph`) with explicit edge types
  (`requires_completion`, `requires_arrival_by`, `benefits_from`), invalid-
  reference detection, and **cycle detection**.
- A longest-path **critical-path** engine (`rippleback.critical_path`).
- Seeded, deterministic scenario fixtures with validation, including a staffed
  variant (`demo-release-plan-staffed`) and two triage-oriented fixtures
  (`project-full`, `travel`) whose resource/effort/duration/slack values are
  **synthetic demo data**.

Explicitly **not implemented** (do not assume they exist): deferring dependent
work, committing a reassignment or a recovery back to the plan, release-date
optimization or any calendar-based scheduling, AI / Amazon Bedrock, a frontend,
and persisting disruptions, recovery options, forecasts, evaluations, or
workload rebalances into the plan. Substitution is supported **only** as an
explicit `metadata.substitutes` declaration read by `find_substitute_options`
(no shipped fixture declares candidates, and no availability, compatibility,
cost, or risk for a substitute is ever inferred or invented). Forecasts are
relative critical-path comparisons in steps or `duration_units` — never
wall-clock dates. The critical path is a relative ranking of dependency chains,
not a date.

## 2. Prerequisites

- Python **3.10+** (developed and verified on **Python 3.15**, macOS arm64).
- Official Python MCP SDK **`mcp==2.3.0`**.
  Note: v2.x renamed the v1 `FastMCP` class to `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`). Follow v2 docs, not v1.
- **`networkx`** (≥ 3.0) for the typed dependency graph and critical path.

## 3. Environment setup

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 4. Install dependencies

Runtime dependencies are pinned in `requirements.txt`:

```bash
pip install -r requirements.txt
pip install -e .
```

To run the tests, also install the `dev` extra (declared in
`pyproject.toml` under `[project.optional-dependencies]`):

```bash
pip install -e ".[dev]"
```

## 5. Start the server

```bash
python -m rippleback.server
# or, after `pip install -e .`:
rippleback-mcp
```

### Starting everything (MCP server + assistant adapter + dashboard)

The conversational dashboard (Person 4) runs three processes:

```bash
# terminal 1 — the MCP server (business logic, 10+ read-only tools)
python -m rippleback.server                # http://127.0.0.1:8000/mcp

# terminal 2 — the local API adapter (the genuine MCP client + assistant)
pip install -e .
rippleback-adapter                         # http://127.0.0.1:8001
# equivalently: python -m rippleback.adapter

# terminal 3 — the React dashboard (proxies /api/* to the adapter)
cd frontend && npm install && npm run dev  # http://127.0.0.1:5173
```

The dashboard works without the adapter/MCP server too: it falls back to
labelled mock data and reports the MCP connection as offline.

## 6. Endpoint and transport

| Setting | Value |
|---------|-------|
| Transport | Streamable HTTP |
| Host | `127.0.0.1` (localhost) — override with `RIPPLEBACK_HOST` |
| Port | `8000` (override with `RIPPLEBACK_PORT`) |
| Path | `/mcp` |
| **Endpoint** | **`http://127.0.0.1:8000/mcp`** |

See `.env.example` for the environment variables. No credentials are used.

## 7. Running the tests

```bash
python -m pytest                       # all tests (unit + integration)
python -m pytest tests/ -q             # unit tests only
python -m pytest tests/integration/ -q # MCP integration tests only
```

If the package is not installed with `pip install -e .`, run pytest with the
`src` directory on the path (PowerShell: `$env:PYTHONPATH="src"`; bash:
`PYTHONPATH=src`).

Unit tests live in `tests/test_scenarios.py` (fixture + validation),
`tests/test_impact.py` (disruption impact engine), `tests/test_recovery.py`
(recovery alternatives), `tests/test_evaluation.py` (proposal evaluation),
`tests/test_workload.py` (workload rebalancing), `tests/test_graph.py` (typed
graph + cycle detection), `tests/test_critical_path.py` (critical path), and
`tests/test_triage.py` (blocker triage + recovery view).

Person 3 engine tests live in `tests/test_forecast.py` (delivery forecast),
`tests/test_constraints.py` (hard-constraint validation),
`tests/test_scoring.py` (option scoring/ranking),
`tests/test_comparison.py` (option comparison with forecasts), and
`tests/test_substitute_recovery.py` (substitute-candidate options).
`tests/test_milestone6_integration.py` covers the MCP tool registration
contract, the server wrappers, and the end-to-end payment-API-outage engine
pipeline on real fixtures.

The integration test (`tests/integration/test_mcp_server.py`) is a **real,
non-mocked** end-to-end test: it spawns a live server subprocess and talks to
it with the official MCP client SDK.

Person 4 (MCP client + assistant) tests:

- `tests/test_client.py` — genuine MCP client against a live server
  subprocess: connection lifecycle, live tool discovery (dynamic; no
  hardcoded tool list), `get_plan_state` / `analyze_blocked_tasks` /
  recovery-tool invocation, invalid tool names and arguments, MCP tool-error
  propagation, server-unavailable handling, transport-failure recovery,
  session cleanup, and sequential/concurrent requests.
- `tests/test_assistant.py` — intent mapping, clarification questions,
  live-data entity resolution, multi-tool workflows built from actual
  intermediate results, failed-step handling, the per-turn tool-call budget,
  scheduling-tool honesty (no fabricated schedules), structural-vs-business
  honesty, the deterministic/Bedrock provider fallback, and one genuine
  end-to-end workflow against a live MCP server that asserts the committed
  plan is never mutated.
- `tests/test_adapter.py` — the local API adapter over real HTTP against a
  live MCP server: health, tool discovery with schemas, tool invocation,
  assistant chat, malformed-input rejection, MCP-offline behaviour (503 +
  honest assistant reply), and the read-only guarantee.

**Two-terminal workflow** (test against a server you started yourself):

```bash
# terminal 1
python -m rippleback.server            # http://127.0.0.1:8000/mcp

# terminal 2
RIPPLEBACK_TEST_URL="http://127.0.0.1:8000/mcp" python -m pytest tests/integration/ -q
```

If `RIPPLEBACK_TEST_URL` is unset, the test starts and stops its own server.

## 8. Example `get_plan_state` call and response

Input (tool arguments):

```json
{ "scenario_id": "demo-release-plan" }
```

Response (actual output, abridged only by whitespace):

```json
{
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "version": 1,
  "status": "healthy",
  "entities": [
    { "id": "payment-api", "name": "Payment API service", "status": "done" },
    { "id": "backend-integration", "name": "Backend integration", "status": "done" },
    { "id": "integration-testing", "name": "Integration testing", "status": "done" },
    { "id": "frontend-integration", "name": "Frontend integration", "status": "in_progress" },
    { "id": "release-validation", "name": "Release validation", "status": "pending" },
    { "id": "production-release", "name": "Production release", "status": "pending" }
  ],
  "dependencies": [
    { "dependent": "backend-integration", "prerequisite": "payment-api" },
    { "dependent": "integration-testing", "prerequisite": "backend-integration" },
    { "dependent": "release-validation", "prerequisite": "integration-testing" },
    { "dependent": "production-release", "prerequisite": "release-validation" }
  ],
  "constraints": [
    {
      "type": "all_entities_done_before",
      "entity_ids": ["production-release"],
      "description": "Every other entity must be done before production-release."
    }
  ],
  "disruptions": []
}
```

The response is always a deep copy — callers cannot mutate the seeded plan.

## 9. Example `simulate_disruption` call and response

Hypothetical only: the committed plan is **not** modified and its `version`
stays the same. Input (tool arguments):

```json
{
  "scenario_id": "demo-release-plan",
  "entity_id": "payment-api",
  "reason": "service outage",
  "severity": "high"
}
```

Response (actual output, abridged only by whitespace):

```json
{
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "plan_version": 1,
  "simulated": true,
  "disruption": {
    "entity_id": "payment-api",
    "name": "Payment API service",
    "state": "blocked",
    "reason": "service outage",
    "severity": "high"
  },
  "directly_affected": [
    {
      "entity_id": "backend-integration",
      "name": "Backend integration",
      "state": "blocked",
      "evidence_path": ["payment-api", "backend-integration"]
    }
  ],
  "transitively_affected": [
    {
      "entity_id": "integration-testing",
      "name": "Integration testing",
      "state": "blocked",
      "evidence_path": ["payment-api", "backend-integration", "integration-testing"]
    },
    {
      "entity_id": "release-validation",
      "name": "Release validation",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation"
      ]
    },
    {
      "entity_id": "production-release",
      "name": "Production release",
      "state": "blocked",
      "evidence_path": [
        "payment-api",
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ]
    }
  ],
  "unaffected": [
    {
      "entity_id": "frontend-integration",
      "name": "Frontend integration",
      "state": "in_progress"
    }
  ]
}
```

Impact follows dependency edges from the target: everything that (transitively)
requires an unavailable entity is `blocked`. Independent work stays healthy —
`frontend-integration` remains `in_progress`. Each affected entity carries an
`evidence_path` explaining the chain. `severity` is one of
`low`/`medium`/`high`/`critical` (default `high`).

## 10. Example `find_alternative_dependencies` call and response

Suggestions only: the committed plan is **not** modified. Input (tool
arguments):

```json
{ "scenario_id": "demo-release-plan", "entity_id": "payment-api" }
```

Response (actual output):

```json
{
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "plan_version": 1,
  "simulated": true,
  "disruption": {
    "entity_id": "payment-api",
    "name": "Payment API service"
  },
  "options": [
    {
      "option_id": "drop-dependency:backend-integration->payment-api",
      "description": "Bypass the dependency 'backend-integration' requires 'payment-api' so 'backend-integration' can proceed without the disrupted prerequisite.",
      "affected_entity_id": "backend-integration",
      "affected_dependency": {
        "dependent": "backend-integration",
        "prerequisite": "payment-api"
      },
      "alternative": {
        "action": "remove_dependency",
        "removed_dependency": {
          "dependent": "backend-integration",
          "prerequisite": "payment-api"
        },
        "dependencies": [
          {
            "dependent": "integration-testing",
            "prerequisite": "backend-integration"
          },
          {
            "dependent": "release-validation",
            "prerequisite": "integration-testing"
          },
          {
            "dependent": "production-release",
            "prerequisite": "release-validation"
          }
        ]
      },
      "unblocks": [
        "backend-integration",
        "integration-testing",
        "release-validation",
        "production-release"
      ],
      "assumptions": [
        "Assumes 'backend-integration' does not strictly require 'payment-api' for this release.",
        "Assumes the removed prerequisite can be satisfied out-of-band or is no longer needed."
      ],
      "limitations": [
        "Hypothetical only; the committed plan is not changed.",
        "Removing a hard prerequisite may break integration guarantees for 'backend-integration'.",
        "Requires human review before adoption."
      ]
    }
  ]
}
```

Each option proposes **bypassing one broken dependency edge** for a directly
affected entity and lists the entities it would `unblocks`. `unblocks` is
computed on the **residual graph** — the committed dependencies with that edge
removed: an entity is listed only if the disruption can no longer reach it, so a
dependent that still requires another blocked prerequisite is not counted as
ready. Independent work (`frontend-integration`) never appears. Options follow
fixture dependency order, so repeated calls return identical results. A valid
entity with no dependents (e.g. `production-release`) yields `"options": []`.

## 11. Example `evaluate_recovery_plan` call and response

Read-only: the committed plan is **not** modified. The proposal is the recovery
`option_id` from §10 plus the plan version it was built against. Input (tool
arguments):

```json
{
  "scenario_id": "demo-release-plan",
  "option_id": "drop-dependency:backend-integration->payment-api",
  "expected_plan_version": 1
}
```

Response (actual output):

```json
{
  "evaluation_id": "eval:demo-release-plan:drop-dependency:backend-integration->payment-api:v1",
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "plan_version": 1,
  "expected_plan_version": 1,
  "verdict": "structurally_valid",
  "dependency": {
    "dependent": "backend-integration",
    "prerequisite": "payment-api"
  },
  "findings": [
    {
      "code": "plan_version_match",
      "level": "info",
      "message": "expected_plan_version 1 matches the committed plan version 1."
    },
    {
      "code": "dependency_edge_exists",
      "level": "info",
      "message": "dependency edge 'backend-integration' requires 'payment-api' exists in the committed plan."
    },
    {
      "code": "business_readiness_not_assessed",
      "level": "warning",
      "message": "Structural validity only: business readiness (integration contracts, substitute services, delivery constraints) is not assessed and must be reviewed by a human."
    }
  ],
  "impact": {
    "disrupted_entity_id": "payment-api",
    "removed_dependency": {
      "dependent": "backend-integration",
      "prerequisite": "payment-api"
    },
    "unblocks": [
      "backend-integration",
      "integration-testing",
      "release-validation",
      "production-release"
    ],
    "still_blocked": []
  },
  "assumptions": [
    "Assumes 'payment-api' is the only unavailable entity (single-disruption model).",
    "Assumes the committed dependency graph is authoritative for dependency ordering."
  ],
  "limitations": [
    "Structural validity does not imply business readiness; removing a hard prerequisite may break integration guarantees.",
    "No substitute candidates, cost/effort ranking, or scheduling are evaluated.",
    "Read-only: the committed plan is not changed and no recovery is committed."
  ]
}
```

`verdict` is one of `structurally_valid` or `rejected`. `structurally_valid`
only means the edge exists, references resolve, and the version matches — it is
**never** a claim that the recovery is safe. On branching graphs `unblocks` /
`still_blocked` come from the residual graph (§5), so removing one prerequisite
does not mark a dependent ready while another prerequisite is still blocked.
Proposals that fail a structural check (stale version, unknown entity, missing
edge) return `verdict: "rejected"` with `impact: null`.

## 12. Example `rebalance_workload` call and response

Read-only and hypothetical: the committed plan is **not** modified. Requires a
**staffed** scenario (one that declares `resources` and per-entity
`assignee`/`effort`). Input (tool arguments):

```json
{
  "scenario_id": "demo-release-plan-staffed",
  "expected_plan_version": 1
}
```

Response (actual output, abridged only by whitespace):

```json
{
  "scenario_id": "demo-release-plan-staffed",
  "plan_id": "plan-payment-api-release-staffed",
  "plan_version": 1,
  "expected_plan_version": 1,
  "simulated": true,
  "unit": "work_units",
  "outcome": "ok",
  "resources": [
    {
      "resource_id": "alice",
      "name": "Alice (synthetic)",
      "capacity": 3,
      "assigned_effort": 4,
      "free_capacity": -1,
      "overloaded": true,
      "assigned_entity_ids": ["release-validation", "production-release"]
    },
    {
      "resource_id": "bob",
      "name": "Bob (synthetic)",
      "capacity": 3,
      "assigned_effort": 2,
      "free_capacity": 1,
      "overloaded": false,
      "assigned_entity_ids": ["frontend-integration"]
    }
  ],
  "suggestions": [
    {
      "suggestion_id": "rebalance:production-release->bob",
      "entity_id": "production-release",
      "effort": 1,
      "from_resource_id": "alice",
      "to_resource_id": "bob",
      "reason": "Resource 'alice' is over capacity (4 > 3 work_units); 'production-release' (1 work_units) is reassigned to 'bob', which has 1 work_units free.",
      "assumptions": [
        "Assumes resource 'bob' can accept 'production-release'; skills and availability are not modeled."
      ],
      "limitations": [
        "Hypothetical only; committed assignments are not changed.",
        "Does not assert any deadline or delivery date improves."
      ]
    }
  ],
  "unresolved": [],
  "assumptions": [
    "Capacity and effort use an abstract 'work_units' scale with no time dimension; no durations or schedules are modeled.",
    "Any resource is assumed able to perform any task; skills, roles, and availability are not modeled."
  ],
  "limitations": [
    "Suggestions are greedy in scenario order and may relocate more effort than the strict minimum needed to clear an overload.",
    "Does not predict or claim any delivery date, deadline, or forecast improvement.",
    "Read-only: committed assignments, dependencies, statuses, and the plan version are never changed.",
    "Synthetic demo data; not real team capacity, effort, or availability."
  ]
}
```

Only **unfinished** work (`pending`/`in_progress`/`blocked`) counts toward a
resource's load; `done` work is excluded. `overloaded` is `true` when unfinished
assigned effort exceeds capacity. Suggestions use stable ids
(`rebalance:{entity_id}->{resource_id}`) and are greedy in scenario order. When
no resource has enough free capacity, the task stays on its resource and appears
in `unresolved` instead. Passing `resource_id` scopes which resource's overload
is addressed (any resource may still receive work). A scenario without staffing
data returns `outcome: "insufficient_data"` rather than a silent empty result.

## 13. Example `analyze_blocked_tasks` call and response (blocker triage)

Hypothetical and read-only: the committed plan is **not** modified. This is
BuildBack's headline feature: after a disruption every entity gets exactly one
state so the team stops only what must stop. Input (tool arguments), using the
`project-full` triage fixture:

```json
{
  "scenario_id": "project-full",
  "entity_id": "payment-api",
  "event_type": "service_outage",
  "reason": "service outage"
}
```

Response (actual output, abridged only by whitespace):

```json
{
  "scenario_id": "project-full",
  "plan_id": "plan-payment-api-release-full",
  "plan_version": 1,
  "simulated": true,
  "disruption": {
    "entity_id": "payment-api",
    "name": "Payment API service",
    "event_type": "service_outage",
    "reason": "service outage",
    "delay_minutes": null
  },
  "states": [
    {
      "entity_id": "auth-service",
      "name": "Auth service",
      "state": "done",
      "reason": "'auth-service' is already completed, so the disruption does not re-block it.",
      "evidence_path": [],
      "evidence_edges": []
    },
    {
      "entity_id": "payment-api",
      "name": "Payment API service",
      "state": "blocked",
      "reason": "'payment-api' is the disrupted entity; the service outage marks it unavailable.",
      "evidence_path": ["payment-api"],
      "evidence_edges": []
    },
    {
      "entity_id": "backend-integration",
      "name": "Backend integration",
      "state": "blocked",
      "reason": "Hard dependency path from 'payment-api' reaches 'backend-integration' and no alternative satisfies it.",
      "evidence_path": ["payment-api", "backend-integration"],
      "evidence_edges": [
        {
          "dependent": "backend-integration",
          "prerequisite": "payment-api",
          "dependency_type": "requires_completion"
        }
      ]
    },
    {
      "entity_id": "frontend-integration",
      "name": "Frontend integration",
      "state": "healthy",
      "reason": "No dependency path links 'frontend-integration' to the disruption.",
      "evidence_path": [],
      "evidence_edges": []
    },
    {
      "entity_id": "unit-testing",
      "name": "Unit testing",
      "state": "at_risk",
      "reason": "'unit-testing' benefits_from 'integration-testing' but does not require it, so the disruption only adds risk.",
      "evidence_path": ["integration-testing", "unit-testing"],
      "evidence_edges": [
        {
          "dependent": "unit-testing",
          "prerequisite": "integration-testing",
          "dependency_type": "benefits_from"
        }
      ]
    },
    {
      "entity_id": "release-notes",
      "name": "Release notes",
      "state": "at_risk",
      "reason": "'release-notes' has only 30 minute(s) of slack, at or below the 60-minute at-risk threshold.",
      "evidence_path": ["release-notes"],
      "evidence_edges": []
    }
  ],
  "summary": { "done": 1, "blocked": 5, "external": 0, "at_risk": 2, "healthy": 2 },
  "parallel_work": [
    { "entity_id": "vendor-delivery", "name": "Vendor deliverable" },
    { "entity_id": "frontend-integration", "name": "Frontend integration" },
    { "entity_id": "unit-testing", "name": "Unit testing" },
    { "entity_id": "release-notes", "name": "Release notes" }
  ]
}
```

(The example is abridged: `release-validation`, `production-release`, and
`vendor-delivery` entries, plus the `critical_path`, `critical_path_change`,
`assumptions`, and `limitations` fields, are omitted here.)

State precedence is `done` > `blocked`/`external` > `at_risk` > `healthy`:

- **`done`** — already completed (`status == "done"`); never re-labelled blocked.
- **`blocked`** — a hard `requires_*` path leads back to the disrupted entity and
  the dependent has no slack to absorb the disruption.
- **`external`** — blocked, but a party on the evidence path is flagged
  `metadata.external` (outside the team), so the team cannot fix it directly.
  Trigger a `delivery_delay` on `vendor-delivery` to see this.
- **`at_risk`** — not blocked, but linked by a soft `benefits_from` edge, or with
  slack at or below the at-risk threshold (60 minutes; open question).
- **`healthy`** — no path from the disruption.

`parallel_work` lists everything that can still proceed (`healthy` + `at_risk`),
which is how the team avoids stopping unnecessarily. `evidence_path` lists the
entity chain from the disruption to the affected entity and `evidence_edges`
names each dependency, so 100% of states are traceable.

**Time-aware blockage.** `service_outage` and `resource_unavailable` are
unbounded: every hard-path entity is blocked. `delivery_delay` and
`transport_delay` carry `delay_minutes`; a dependent absorbs the delay while its
`metadata.slack_minutes` lasts, and the delay propagates in topological order, so
a downstream event is **not** assumed to fail when a buffer absorbs the delay.
On the `travel` fixture, a 120-minute delay on `leg-1` blocks `transfer` and
`connecting-leg` but leaves `hotel-check-in` `at_risk` and `meeting` `healthy`.

**Recovery view.** `rippleback.triage.recompute_with_changes(scenario, entity_id,
changes)` re-runs triage on a modified plan and reports `state_changes`, so a
change set that removes the broken edge (for example a mock service) shows the
blocked-to-healthy payoff for Person 3 without ever touching the committed plan.

## 14. Typed dependency graph and critical path

`rippleback.graph.build_graph(scenario)` builds a directed, typed graph
`G = (V, E)` with NetworkX. Edges run prerequisite → dependent and carry an
explicit `dependency_type`:

- `requires_completion` (hard) — dependent cannot finish until the prerequisite
  does.
- `requires_arrival_by` (hard) — dependent must start after the prerequisite,
  subject to `constraint_parameters`.
- `benefits_from` (soft) — only adds risk; never blocks.

Edges without a `dependency_type` default to `requires_completion`, so older
fixtures keep working. Validation raises `GraphError` with a stable code —
`ENTITY_NOT_FOUND`, `INVALID_DEPENDENCY`, `INVALID_DEPENDENCY_TYPE`, or
`CYCLIC_DEPENDENCY` — and `graph_errors()` returns the same faults as coded dicts
without raising. `validate_scenario()` also detects cycles, so
`get_plan_state` refuses a cyclic fixture.

`rippleback.critical_path.critical_path(scenario)` returns the longest hard
dependency chain (`path`, `length`, `unit`, `edges`); it is weighted by entity
`duration` when present, otherwise counted in steps. `critical_path_changes`
reports the critical path before the disruption and the longest chain within the
disruption's impact set. This is a relative ranking of dependency chains, **not**
a delivery date.

## 15. Unknown scenario IDs and invalid proposals

`scenario_id` is required. An unknown, empty, or malformed id is rejected
predictably; the call returns `is_error: true` with a clear message. **No
fallback to another scenario ever occurs.** Verified message text:

```
Error executing tool get_plan_state: unknown scenario_id 'no-such-scenario'; available: ['demo-release-plan', 'demo-release-plan-staffed', 'project-full', 'travel']
```

`simulate_disruption` also rejects unknown `entity_id` values, an empty
`reason`, and an unsupported `severity`; `find_alternative_dependencies` rejects
unknown `entity_id` values. Each returns a structured error, e.g.:

```
Error executing tool simulate_disruption: unknown entity_id 'ghost'; available: [...]
Error executing tool find_alternative_dependencies: unknown entity_id 'ghost'; available: [...]
```

`evaluate_recovery_plan` raises a structured error for an unknown `scenario_id`
and for a **malformed proposal** (bad `option_id` or a non-integer
`expected_plan_version`):

```
Error executing tool evaluate_recovery_plan: malformed option_id 'not-an-option'; expected 'drop-dependency:' followed by '{dependent}->{prerequisite}'
```

A well-formed proposal that fails a structural check is **not** an error — it
returns a `rejected` report (`is_error: false`) with the failing `findings`
(`stale_plan_version`, `unknown_entity`, or `missing_dependency`).

`rebalance_workload` raises a structured error for an unknown `scenario_id`, an
unknown `resource_id`, and a **stale or non-integer `expected_plan_version`**:

```
Error executing tool rebalance_workload: unknown resource_id 'ghost'; available: ['alice', 'bob']
Error executing tool rebalance_workload: stale expected_plan_version 99; committed plan version is 1
```

A scenario without staffing data is **not** an error — it returns
`outcome: "insufficient_data"` (`is_error: false`) so the absence of data is
explicit rather than a misleading empty rebalance.

`analyze_blocked_tasks` raises a structured error for an unknown `scenario_id`,
an unknown `entity_id`, an unsupported `event_type`, and a delay event without a
positive `delay_minutes`:

```
Error executing tool analyze_blocked_tasks: unknown entity_id 'ghost'; available: [...]
Error executing tool analyze_blocked_tasks: unsupported event_type 'meteor'; supported: ['delivery_delay', 'resource_unavailable', 'service_outage', 'transport_delay']
Error executing tool analyze_blocked_tasks: event_type 'delivery_delay' requires a positive delay_minutes
```

Internally `get_scenario()` raises `ScenarioError`, the impact engine raises
`ImpactError`, the recovery engine raises `RecoveryError`, the evaluation engine
raises `EvaluationError`, the workload engine raises `WorkloadError`, `graph.py`
raises `GraphError`, and `triage.py` raises `TriageError`; all are converted to
the SDK's `ToolError` so the message reaches the model/client.

## 16. Current limitations / not yet implemented

- **Four fixed scenarios**: `demo-release-plan` and its staffed variant
  `demo-release-plan-staffed` (which keep the chain `done`), plus the triage
  fixtures `project-full` (unfinished release chain) and `travel` (delay
  absorption). No dynamic plan loading or editing.
- **Healthy committed plan**: `status` is always `healthy` and the committed
  `disruptions` is always `[]`. Every analysis tool (simulation, recovery,
  evaluation, workload rebalancing, blocker triage, forecasting, constraint
  validation, option comparison, substitute discovery) is hypothetical/read-only
  and is never persisted.
- **`simulate_disruption` impact model is simplified**: any transitively affected
  entity is reported as `blocked`, regardless of its committed status (a
  completed entity can appear as blocked in a simulation). For committed-status-
  aware, time-aware triage use `analyze_blocked_tasks` instead.
- **Blocker triage is heuristic**: the at-risk slack threshold (60 minutes) and
  the hard/soft edge semantics are demo choices; `external` is read from
  `metadata.external` (no integrations); delay propagation is deterministic
  topological arithmetic, not a scheduler.
- **Recovery semantics are narrow**: an "alternative dependency" means removing
  the broken edge; substitution is a separate, explicitly declared option —
  `find_substitute_options` only proposes candidates a scenario lists in
  `metadata.substitutes` (no shipped fixture declares any, and compatibility,
  availability, cost, or risk for a candidate are never invented). There is no
  multi-step recovery planning, and ranking scores delay only by default: the
  repository supplies no cost or risk data, so a nonzero weight on those
  metrics without caller-supplied values yields an unscorable option, never a
  fabricated score.
- **Evaluation is structural only**: `evaluate_recovery_plan` answers whether a
  proposal is *applicable* (edge exists, references resolve, version matches).
  It does **not** assess business readiness — that is deliberately out of scope
  and reported as an explicit warning. `unblocks`/`still_blocked` are
  single-disruption, residual-graph claims.
- **Workload rebalancing is synthetic and coarse**: only the staffed demo
  scenario has resources; capacity/effort are abstract integer `work_units` with
  no time dimension. Skills, roles, availability, partial moves, and scheduling
  are unmodeled; suggestions are greedy and may relocate more than the minimum.
  The workload engine never produces a delivery date.
- **No writes**: no committing a reassignment, release-date optimization, or
  committing a recovery/evaluation/rebalance/forecast back to the plan.
- **No AI**: no LLM, no Amazon Bedrock, no inference of any kind.
- **No calendar scheduling**: `generate_recovery_schedule` produces a
  *relative-duration* schedule (readiness groups, parallel layers, execution
  order, start/finish offsets from the moment of analysis) — but no calendars,
  working hours, timezones, or dates exist in the repository, so no
  wall-clock scheduling, deadline optimization, or resource leveling is
  possible or claimed.
- **No frontend** and no persistence (fixtures are in-process Python dicts).
- **No auth**: server binds to localhost with no authentication — never expose
  it publicly.
- **Validation is structural**: schema/ref checks only; no semantic rules yet.

## 17. Forecast, constraint, comparison, and substitute tools (Milestone 6)

Four read-only tools expose the Person 3 recovery engines. All are
hypothetical: the committed plan, statuses, dependencies, and `version` are
never modified, and every report is deterministic and JSON-serializable.

### `recalculate_delivery_forecast(scenario_id, changes=None, option_id=None, disrupted_entity_id=None)`

Compares the committed critical path with the critical path after a proposed
change set (the `apply_changes` shape: `remove_dependency`/`add_dependency`
entries), or converts a legacy `drop-dependency:{dependent}->{prerequisite}`
`option_id` into the equivalent removal. `delay_delta` = forecast length −
baseline length in the reported `unit` — `steps` or `duration_units` — **never
a calendar date**. A well-formed change set that would create an invalid graph
(cycle, unknown reference, self-dependency) returns `feasible: false` with the
stable graph error code and `null` forecast fields; it is never reported as a
valid forecast. Substitute option ids (`substitute:...`) are rejected with a
structured error directing the caller to pass the option's explicit `changes`
(the legacy id parser only understands `drop-dependency:`).

### `validate_hard_constraints(scenario_id, changes=None)`

Validates hard constraints and graph rules for a proposed change set
(`changes=None` validates the committed plan as baseline). Returns `feasible`
plus `violations`: graph faults first (`ENTITY_NOT_FOUND`,
`INVALID_DEPENDENCY`, `INVALID_DEPENDENCY_TYPE`, `CYCLIC_DEPENDENCY`), then
committed constraints in fixture order. The only supported constraint type is
`all_entities_done_before`, evaluated as a **current-state invariant** over
committed statuses (a listed gate must not be `done` while any other entity is
not `done`); unsupported types are reported as violations, never silently
skipped. `feasible: true` means every graph rule passed and every constraint
was supported and currently satisfied. No schedule or calendar is modeled.

### `compare_recovery_options(scenario_id, options, weights=None)`

Gates each option with `validate_hard_constraints`, scores, ranks, and attaches
a per-option forecast. Score formula (lower is better):

```
score = w_delay·delay + w_cost·cost + w_risk·risk
      + w_strain·resource_strain + w_count·disruption_count
```

Default weights: `delay` **1.0**, everything else **0.0**. `delay` is derived
from the forecast delta; `resource_strain` from workload overload when staffing
data exists; `cost`, `risk`, and `disruption_count` have **no source in the
repository** and are never fabricated — a nonzero weight without a supplied
value makes an option *unscorable* (excluded with a reason), never scored with
a fabricated zero. Infeasible options are excluded with their violations and
are **never recommended**. `recommendation` is the top feasible, scorable
option (ties break by ascending `option_id`) or `null` with
`outcome: "no_feasible_recovery"`. Options may carry explicit `changes`
(including substitute options) or use the legacy `drop-dependency:` id.

### `find_substitute_options(scenario_id, entity_id)`

Proposes substitute-service options **only** from an explicit
`metadata.substitutes` declaration on the disrupted entity — a list of entity
ids in the same scenario. Nothing is inferred from names, statuses, resources,
or graph structure. Each declared candidate becomes one
`substitute:{prerequisite}->{substitute}` option whose change set reroutes
every dependency edge on the disrupted entity (one `remove_dependency` plus one
`add_dependency` per dependent, preserving `dependency_type` and
`constraint_parameters`), validated against hard constraints and graph rules.
Outcomes: `substitutes_proposed`, `no_feasible_substitute`, or
`no_substitute_candidates` (with `missing_data` explaining what is absent).
**No shipped fixture declares `metadata.substitutes`**, so real fixtures
return `no_substitute_candidates`; no service, availability, compatibility,
cost, or risk is ever invented. Options carry explicit `changes`, so they flow
into `compare_recovery_options` and `recalculate_delivery_forecast` unchanged.

## 18. Recovery scheduling tool (Milestone 7)

### `generate_recovery_schedule(scenario_id, changes=None, option_id=None, disrupted_entity_id=None, scheduling_config=None)`

A **relative-duration** scheduling engine (`rippleback.scheduler`) reusing the
graph, triage, constraints, and critical-path engines — no second graph or
constraint engine is introduced. With no `changes`/`option_id` it describes
the committed plan's schedule; with a change set (or a legacy
`drop-dependency:` id) it also reports the committed plan's `baseline`
schedule and a `comparison`. When `disrupted_entity_id` is given, readiness
comes from blocker triage (default `service_outage` event) recomputed against
the **modified** graph, so a recovery that removes the cause of a blockage
shows as hypothetical unblocked work, clearly distinct from the committed
state.

Rules the engine enforces:

- **Relative time only** — `t = 0` is the moment of analysis; the repository
  models no calendar, start date, or working hours, so every time is a
  relative offset in the report's unit (`duration_units` by default).
- **Durations are never invented** — when any entity lacks a `duration`, the
  report is ordering-only: `outcome: "insufficient_duration_data"`,
  `null` start/finish/completion times, and `missing_data` naming the
  entities. `scheduling_config["durations"]` supplies explicit per-entity
  overrides for the hypothetical report.
- **Hard vs. soft edges** — `requires_completion`/`requires_arrival_by` edges
  constrain ordering; `benefits_from` never does. `required_buffer_minutes`
  arrival buffers are added to start times only when the caller explicitly
  declares `scheduling_config["duration_unit"]` as `"minutes"` (otherwise
  they are preserved, reported, and deliberately not mixed into unitless
  durations).
- **Completed/blocked work** — committed `done` tasks are complete at `t = 0`
  and never restarted; committed `blocked` tasks and triage
  `blocked`/`external` tasks are not scheduled (their finish is unknown and
  the completion duration covers only schedulable work — see
  `completion.covers_all_tasks`).
- **Infeasible proposals are never ordered** — a change set that creates a
  cycle, a dangling reference, or a hard-constraint violation (the
  `validate_hard_constraints` engine is reused) returns `feasible: false`,
  `outcome: "infeasible"`, the stable violations, and `null`
  schedule/comparison fields.
- **Comparable-only deltas** — `completion_delta` is computed only when both
  sides have the same valid unit and durations; otherwise the comparison is
  marked `incomparable` with a reason. When the two sides exclude different
  tasks (different blocked/waiting sets), the excluded lists are reported and
  the limitation text says the delta covers different work sets.

Example (recovery of `project-full` after a `vendor-delivery` outage,
removing the vendor dependency from `backend-integration`):

```jsonc
// generate_recovery_schedule("project-full",
//   changes=[{"action": "remove_dependency",
//             "dependent": "backend-integration",
//             "prerequisite": "vendor-delivery"}],
//   disrupted_entity_id="vendor-delivery")
{
  "scenario_id": "project-full",
  "plan_version": 1,
  "simulated": true,
  "disrupted_entity_id": "vendor-delivery",
  "feasible": true,
  "outcome": "scheduled",
  "unit": "duration_units",
  "schedule": [
    {"entity_id": "auth-service",         "group": "completed", "layer": 0, "duration": 2.0, "earliest_start": 0.0, "earliest_finish": 0.0, "waiting_time": 0.0, "on_critical_path": false},
    {"entity_id": "payment-api",          "group": "ready_now", "layer": 1, "duration": 2.0, "earliest_start": 0.0, "earliest_finish": 2.0, "waiting_time": 0.0, "on_critical_path": true},
    {"entity_id": "vendor-delivery",      "group": "blocked",   "layer": null, "duration": 1.0, "earliest_start": null, "earliest_finish": null, "waiting_time": null, "on_critical_path": false},
    {"entity_id": "backend-integration",  "group": "ready_now", "layer": 2, "duration": 3.0, "earliest_start": 2.0, "earliest_finish": 5.0, "waiting_time": 0.0, "on_critical_path": true},
    {"entity_id": "integration-testing",  "group": "ready_now", "layer": 3, "duration": 2.0, "earliest_start": 5.0, "earliest_finish": 7.0, "waiting_time": 0.0, "on_critical_path": true},
    {"entity_id": "release-validation",   "group": "ready_now", "layer": 4, "duration": 1.0, "earliest_start": 7.0, "earliest_finish": 8.0, "waiting_time": 0.0, "on_critical_path": true},
    {"entity_id": "production-release",   "group": "ready_now", "layer": 5, "duration": 1.0, "earliest_start": 8.0, "earliest_finish": 9.0, "waiting_time": 0.0, "on_critical_path": true},
    {"entity_id": "frontend-integration", "group": "ready_now", "layer": 1, "duration": 2.0, "earliest_start": 0.0, "earliest_finish": 2.0, "waiting_time": 0.0, "on_critical_path": false},
    {"entity_id": "unit-testing",         "group": "ready_now", "layer": 1, "duration": 1.0, "earliest_start": 0.0, "earliest_finish": 1.0, "waiting_time": 0.0, "on_critical_path": false},
    {"entity_id": "release-notes",        "group": "ready_now", "layer": 1, "duration": 1.0, "earliest_start": 0.0, "earliest_finish": 1.0, "waiting_time": 0.0, "on_critical_path": false}
  ],
  "groups": {
    "completed": ["auth-service"],
    "ready_now": ["payment-api", "backend-integration", "integration-testing",
                  "release-validation", "production-release",
                  "frontend-integration", "unit-testing", "release-notes"],
    "waiting": [],
    "blocked": ["vendor-delivery"]
  },
  "execution_order": ["payment-api", "frontend-integration", "unit-testing",
                      "release-notes", "backend-integration",
                      "integration-testing", "release-validation",
                      "production-release"],
  "parallel_groups": [
    {"layer": 1, "entity_ids": ["payment-api", "frontend-integration", "unit-testing", "release-notes"]},
    {"layer": 2, "entity_ids": ["backend-integration"]},
    {"layer": 3, "entity_ids": ["integration-testing"]},
    {"layer": 4, "entity_ids": ["release-validation"]},
    {"layer": 5, "entity_ids": ["production-release"]}
  ],
  "completion": {"duration": 9.0, "unit": "duration_units",
                 "covers_all_tasks": false,
                 "excluded_tasks": ["vendor-delivery"]},
  "baseline": { /* committed plan's schedule: ready_now has only
                   payment-api, frontend-integration, unit-testing,
                   release-notes; blocked additionally has
                   backend-integration, integration-testing,
                   release-validation, production-release */ },
  "comparison": {
    "comparable": true,
    "baseline_completion_duration": 2.0,
    "proposed_completion_duration": 9.0,
    "completion_delta": 7.0,
    "newly_unblocked": ["backend-integration", "integration-testing",
                         "release-validation", "production-release"],
    "baseline_excluded_tasks": ["vendor-delivery", "backend-integration",
                                "integration-testing", "release-validation",
                                "production-release"],
    "proposed_excluded_tasks": ["vendor-delivery"],
    "parallelism": {"baseline_layer_count": 1, "proposed_layer_count": 5,
                    "baseline_max_group_size": 4, "proposed_max_group_size": 4}
  },
  "violations": [],
  "missing_data": []
}
```

Read the `completion_delta = 7.0` together with `*_excluded_tasks`: the
baseline makespan (2.0) covers only the four tasks that were schedulable
*without* the vendor, while the recovery makespan (9.0) additionally covers
the four newly unblocked tasks — the recovery does not make the project
slower, it makes more of it schedulable. Full `assumptions`, `limitations`,
and per-task `task_offset_changes` are always present in the real response.

## 19. MCP client, local API adapter, and conversational assistant (Person 4)

### Architecture

```
Browser (React dashboard)
   │  REST: /api/health, /api/mcp/tools, /api/mcp/tool, /api/assistant/chat
   ▼
rippleback-adapter  (src/rippleback/adapter.py — Starlette, localhost:8001)
   │  owns the ONE genuine MCP session (official Python MCP SDK, Streamable HTTP)
   ▼
rippleback-mcp      (src/rippleback/server.py — http://127.0.0.1:8000/mcp)
```

The browser never speaks MCP directly and never holds an MCP session. The
adapter is the single genuine MCP client of the application; the previous
hand-rolled Node shim (`frontend/proxy-server.cjs`, raw JSON-RPC without a
session handshake) was removed and replaced by it. The Vite dev server proxies
`/api/*` to the adapter (`RIPPLEBACK_ADAPTER_PORT`, default `8001`).

### MCP client (`src/rippleback/client.py`)

`RippleBackMCPClient` wraps the official SDK (`mcp.ClientSession` +
`mcp.client.streamable_http.streamable_http_client`):

- **Connection management** — one initialized session reused across requests;
  `connect`/`disconnect` lifecycle; transport failures tear the session down
  in-place (never in a fire-and-forget task) and the next call reconnects.
  SDK transport failures that surface as `CancelledError` are converted to
  `MCPConnectionError` unless the task itself is being cancelled.
- **Tool discovery** — `list_tools()` calls the live `tools/list`; names,
  descriptions, and JSON input schemas are cached and refreshable. Nothing is
  hardcoded, so newly registered tools (e.g. a scheduling tool) are discovered
  without any frontend or assistant change.
- **Invocation** — `call_tool()` validates arguments against the discovered
  input schema (required fields, unknown fields, scalar types), performs a
  real `tools/call`, parses the structured JSON result, and raises
  `MCPToolFailedError` on `isError` — tool errors are never converted into
  successful-looking responses.
- **Concurrency** — session access is serialized with an `asyncio.Lock`;
  concurrent requests are safe.
- **Retries** — none for tool calls (future tools may not be read-only);
  failures surface as clear, recoverable errors instead. Read-only
  `tools/list` reconnects automatically on the next use.

### Local API adapter (`src/rippleback/adapter.py`)

`rippleback-adapter` (Starlette + uvicorn, binds `127.0.0.1:8001` by default):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/health` | MCP connection state, discovered tool names, active assistant provider (503 when the MCP server is down) |
| `GET /api/mcp/tools[?refresh=1]` | Live-discovered tools with descriptions + input schemas |
| `POST /api/mcp/tool` | `{tool, arguments}` → `{result}` or `{error, isError: true}` (400 bad input, 404 unknown tool, 503 MCP unreachable) |
| `POST /api/assistant/chat` | `{message, scenario_id?, provider?}` → reply + full tool-call trace |

### Assistant (`src/rippleback/assistant/`)

Pipeline, with each concern separated:

1. **User message** (+ the dashboard's currently selected scenario).
2. **Intent + parameter extraction** — `intents.py` deterministically maps the
   message to one intent and extracts literal parameters (`delay_minutes`,
   `severity`, `option_id`, `expected_plan_version`, `reason`, `event_type`).
   `providers.py` provides the provider abstraction: `DeterministicProvider`
   (default, offline) and optional `BedrockProvider`.
3. **Tool selection** — `orchestrator.py` sequences *live-discovered* MCP
   tools per intent; a scheduling tool is routed by name only if registered.
4. **MCP invocation** — every call goes through the MCP client; no engine
   function is called directly.
5. **Result interpretation** — `explain.py` renders concise Markdown from the
   actual structured results (affected entities, evidence paths, verdicts,
   excluded options, missing data), always labelling hypothetical results.
6. **Reply** — explanation + expandable per-tool trace (arguments, status,
   duration, structured result) in the dashboard's Assistant view.

Supported intents: plan state, disruption simulation, blocker triage,
recovery alternatives, substitutes, proposal evaluation, hard-constraint
validation, option comparison, delivery forecast, workload rebalancing,
recovery scheduling (when a scheduling tool exists), a multi-step recovery
workflow (triage → alternatives → compare, fed with actual intermediate
results), and help.

Safety properties:

- Entity/scenario resolution uses **live `get_plan_state` data**; unknown
  mentions produce a clarification question listing real entities — ids are
  never guessed or hardcoded.
- A per-turn tool-call budget (`RIPPLEBACK_MAX_TOOL_CALLS`, default 8) bounds
  runaway workflows; a failed step stops the chain with an honest explanation.
- No dates, cost, risk, capacity, substitute availability, or schedule
  improvements are ever fabricated; forecasts stay relative
  (steps/duration_units); "structurally valid" is never presented as
  business-safe; nothing is written to the committed plan.

### Assistant providers

`RIPPLEBACK_ASSISTANT_PROVIDER=deterministic|bedrock` (default
`deterministic`). The Bedrock provider is optional and only selected when
configured; it reads `RIPPLEBACK_BEDROCK_REGION` and
`RIPPLEBACK_BEDROCK_MODEL_ID` (no model id is guessed — unset means disabled),
uses the backend's standard AWS credential chain (`pip install -e ".[bedrock]"`
for boto3), sends the model the live tool catalog, validates everything it
returns, and falls back to the deterministic interpreter on any failure
(auth, throttle, timeout, malformed output) — the reply metadata states the
provider actually used and why a fallback happened. The application fully
works without Bedrock credentials.

### Example natural-language workflows

```
"Show me the current plan."
  → get_plan_state

"What happens if the payment API goes down?"
  → get_plan_state (resolve entity) → simulate_disruption

"The payment API is down. Tell me what is blocked, find recovery
 alternatives, and compare the options."
  → get_plan_state → analyze_blocked_tasks →
    find_alternative_dependencies → compare_recovery_options(options=…)
    (the options passed to compare are the ones alternatives returned)

"Evaluate this recovery proposal: drop-dependency:backend-integration->payment-api"
  → get_plan_state (plan version) → evaluate_recovery_plan

"Can you rebalance the workload?"
  → get_plan_state → rebalance_workload

"Generate a recovery schedule after this disruption."
  → honest answer listing the live tools when no scheduling tool is
    registered; automatic routing once one is.
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard shows "MCP offline" | Start `rippleback-mcp`, then `rippleback-adapter`; press Ping in the MCP Inspector. Mock data is clearly labelled while offline. |
| Chat replies "MCP server unreachable" | The adapter is up but the MCP server is not (or `RIPPLEBACK_MCP_URL` is wrong). Fix the server and press Retry. |
| Adapter returns 503 on `/api/health` | Same as above — health reports the *MCP* server's state. |
| Adapter refuses to start / port in use | Set `RIPPLEBACK_ADAPTER_PORT` (and the same value in the frontend env for the Vite proxy target). |
| Bedrock replies say "deterministic fallback" | Set `RIPPLEBACK_BEDROCK_REGION` + `RIPPLEBACK_BEDROCK_MODEL_ID`, install `.[bedrock]`, and ensure the backend's AWS credentials work. |

## Data contract

See [`docs/data-contract.md`](docs/data-contract.md) for the scenario, entity,
dependency, disruption-simulation, recovery-alternative, proposal-evaluation,
workload-rebalancing, blocker-triage, critical-path, forecast,
constraint-validation, scoring/ranking, comparison, and substitute-candidate
schemas and open questions for the team.
