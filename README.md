# RippleBack — MCP server, impact engine, recovery alternatives

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
- A seeded, deterministic scenario fixture with validation.

Explicitly **not implemented** (do not assume they exist): recovery-plan
evaluation, task reassignment, release-date optimization, delivery forecasts,
committing changes, AI / Amazon Bedrock, scheduling, frontend, and persisting
disruptions or recovery options into the plan.

## 2. Prerequisites

- Python **3.10+** (developed and verified on **Python 3.15**, macOS arm64).
- Official Python MCP SDK **`mcp==2.3.0`**.
  Note: v2.x renamed the v1 `FastMCP` class to `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`). Follow v2 docs, not v1.

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

Unit tests live in `tests/test_scenarios.py` (fixture + validation),
`tests/test_impact.py` (disruption impact engine), and `tests/test_recovery.py`
(recovery alternatives).

The integration test (`tests/integration/test_mcp_server.py`) is a **real,
non-mocked** end-to-end test: it spawns a live server subprocess and talks to
it with the official MCP client SDK.

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

## 11. Unknown scenario IDs

`scenario_id` is required. An unknown, empty, or malformed id is rejected
predictably; the call returns `is_error: true` with a clear message. **No
fallback to another scenario ever occurs.** Verified message text:

```
Error executing tool get_plan_state: unknown scenario_id 'no-such-scenario'; available: ['demo-release-plan']
```

`simulate_disruption` also rejects unknown `entity_id` values, an empty
`reason`, and an unsupported `severity`; `find_alternative_dependencies` rejects
unknown `entity_id` values. Each returns a structured error, e.g.:

```
Error executing tool simulate_disruption: unknown entity_id 'ghost'; available: [...]
Error executing tool find_alternative_dependencies: unknown entity_id 'ghost'; available: [...]
```

Internally `get_scenario()` raises `ScenarioError`, the impact engine raises
`ImpactError`, and the recovery engine raises `RecoveryError`; all are converted
to the SDK's `ToolError` so the message reaches the model/client.

## 12. Current limitations / not yet implemented

- **Single scenario**: only `demo-release-plan` exists; no dynamic plan loading.
- **Healthy committed plan**: `status` is always `healthy` and the committed
  `disruptions` is always `[]`. Simulation and recovery are hypothetical only
  and are never persisted.
- **Simplified impact model**: any transitively affected entity is reported as
  `blocked`, regardless of its committed status (a completed entity can appear
  as blocked in a simulation). No severity-weighted or partial impact yet.
- **Recovery semantics are narrow**: an "alternative dependency" currently means
  removing the broken edge only. No substitution candidates, effort/cost
  ranking, or multi-step recovery plans; no recovery-plan evaluation.
- **`unblocks` is structural, single-disruption**: it reflects removal of the
  disruption's dependency path on the residual graph, not business readiness.
  Only one entity is treated as unavailable at a time.
- **No writes**: no task reassignment, release-date optimization, or delivery
  forecasts; nothing is committed back to the plan.
- **No AI**: no LLM, no Amazon Bedrock, no inference of any kind.
- **No scheduling**: no critical-path or timeline scheduling.
- **No frontend** and no persistence (fixtures are in-process Python dicts).
- **No auth**: server binds to localhost with no authentication — never expose
  it publicly.
- **Validation is structural**: schema/ref checks only; no semantic rules yet.

## Data contract

See [`docs/data-contract.md`](docs/data-contract.md) for the scenario, entity,
dependency, disruption-simulation, and recovery-alternative schemas and open
questions for the team.
