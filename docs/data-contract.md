# RippleBack data contract

Source of truth: `src/rippleback/scenarios.py` (plan data), `src/rippleback/impact.py`
(disruption simulation), `src/rippleback/recovery.py` (recovery alternatives and
substitute candidates), `src/rippleback/evaluation.py` (recovery-proposal evaluation),
`src/rippleback/workload.py` (workload rebalancing), `src/rippleback/graph.py`
(typed dependency graph), `src/rippleback/critical_path.py` (critical path),
`src/rippleback/triage.py` (blocker triage), `src/rippleback/forecast.py`
(delivery forecasts), `src/rippleback/constraints.py` (hard-constraint
validation), `src/rippleback/scoring.py` (option scoring/ranking), and
`src/rippleback/comparison.py` (option comparison with forecasts). Plan
validation is enforced by `validate_scenario()` at fixture definition/lookup
time and surfaced through `get_plan_state`.

Phase 1 defined the plan schema (§1–§3). Phase 2 added hypothetical disruption
simulation (§4). Phase 3 added hypothetical recovery alternatives (§5). Phase 4
adds structural evaluation of a recovery proposal (§6). Phase 5 adds hypothetical
workload rebalancing for staffed scenarios (§7). Phase 6 adds blocker triage (§8) and the typed dependency graph + critical path
(§9). Milestone 6 (Person 3 engine integration) adds delivery forecasts (§10),
hard-constraint validation (§11), option scoring/ranking (§12), option
comparison with forecasts (§13), and explicitly declared substitute candidates
(§14), each also exposed as a read-only MCP tool; recovery scheduling adds a
relative-duration scheduling engine (§14.1). All of §4–§14 are read-only and return separate result objects. §7 adds
optional plan fields (`resources`, entity `assignee`/`effort`) and §9 adds
optional graph fields (entity `duration`/`metadata`, dependency `dependency_type`,
constraint `parameters`) but does not change any required field.

Status: **proposed for team review.** Fields marked **OPEN** need a decision —
see [Open questions](#open-questions).

---

## 1. Scenario schema

Top-level object returned (in summarized form) by `get_plan_state`.

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | string (non-empty) | yes | `"demo-release-plan"` |
| `plan_id` | string (non-empty) | yes | `"plan-payment-api-release"` |
| `version` | integer | yes | `1` |
| `status` | enum: `healthy` \| `degraded` \| `disrupted` **OPEN** | yes | `"healthy"` |
| `entities` | list of [Entity](#2-entity-schema) (non-empty) | yes | see below |
| `dependencies` | list of [Dependency](#3-dependency-schema) | yes | see below |
| `constraints` | list of [Constraint](#constraint-schema) | yes | see below |
| `resources` | list of [Resource](#resource-schema) | no (Phase 5) | see §7 |
| `disruptions` | list of disruption | tool output only; committed plan is always `[]` — simulated disruptions live in §4, not here | `[]` |

Example (verified output, truncated):

```json
{
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "version": 1,
  "status": "healthy",
  "entities": [],
  "dependencies": [],
  "constraints": [],
  "disruptions": []
}
```

### Constraint schema

| Field | Type | Required |
|-------|------|----------|
| `type` | string (non-empty) | yes |
| `entity_ids` | list of entity ids (non-empty) | yes |
| `description` | string | no |

Known `type` value: `all_entities_done_before`.

---

## 2. Entity schema

An **entity** is a node in the plan: one unit of work. It has identity and
state. It does **not** describe ordering — that is a dependency.

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `id` | string, stable, non-empty, unique within a scenario | yes | `"payment-api"` |
| `name` | string | yes | `"Payment API service"` |
| `status` | enum: `pending` \| `in_progress` \| `done` \| `blocked` | yes | `"done"` |
| `assignee` | resource id (Phase 5, optional) | no | `"alice"` |
| `effort` | non-negative integer, abstract `work_units` (Phase 5, optional) | no | `3` |
| `duration` | positive number, relative units (Phase 6, optional) | no | `2` |
| `metadata` | object (Phase 6, optional) | no | see below |

Example:

```json
{ "id": "payment-api", "name": "Payment API service", "status": "done" }
```

`assignee`/`effort` are optional Phase 5 staffing fields; an entity is counted in
workload rebalancing (§7) only when **both** are present and `assignee` names a
declared resource. When present, `assignee` must reference an existing
`resources[].id` and `effort` must be a non-negative integer (`bool` excluded).

`duration` and `metadata` are optional Phase 6 fields (both validated):
`duration` must be a positive number (`bool` excluded); `metadata` must be an
object. Recognized `metadata` keys: `external` (a real `true` flags the entity as
owned outside the team — see the `external` state in §8), `slack_minutes`
(a non-negative number of buffer minutes used by delay-absorption and the
at-risk threshold in §8), and `substitutes` (Milestone 6: a list of entity ids
proposed as substitute candidates for this entity — see §14; `validate_scenario`
does not check it, but `find_substitute_options` rejects a malformed value with
a structured error, and no shipped fixture declares it). Unknown keys are
allowed and ignored.

---

## 3. Dependency schema

A **dependency** is a directed edge between two entities. It contains no state
of its own; it only expresses ordering.

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `dependent` | entity id of the work that needs the other first | yes | `"backend-integration"` |
| `prerequisite` | entity id of the work that must come first | yes | `"payment-api"` |
| `dependency_type` | enum: `requires_completion` \| `requires_arrival_by` \| `benefits_from` (Phase 6, optional) | no | `"requires_completion"` |
| `constraint_parameters` | object (Phase 6, optional) | no | `{"required_buffer_minutes": 30}` |

Example:

```json
{ "dependent": "backend-integration", "prerequisite": "payment-api" }
```

Rules (enforced by `validate_scenario()`):

- Both values must reference an existing entity id in the same scenario.
- Self-dependency (`dependent == prerequisite`) is rejected.
- Duplicate `(dependent, prerequisite)` pairs are rejected.
- `dependency_type`, when present, must be one of the three values above.
- `constraint_parameters`, when present, must be an object.
- The graph must be **acyclic** over hard edges (Phase 6, §9); a cycle is
  rejected with `CYCLIC_DEPENDENCY`.

### Edge types (Phase 6)

`dependency_type` classifies the edge:

- **`requires_completion`** (hard) — the dependent cannot finish before the
  prerequisite does. This is the default when `dependency_type` is absent, so
  Phase 1–5 fixtures keep working.
- **`requires_arrival_by`** (hard) — sequencing with a required buffer; may carry
  `constraint_parameters` such as `required_buffer_minutes`. Treated as hard for
  blocking (§8) and critical-path (§9) purposes.
- **`benefits_from`** (soft) — the dependent does not require the prerequisite;
  the edge only adds **risk** (never blocks). An affected soft successor becomes
  `at_risk`, not `blocked` (§8).

### Dependency direction (normative)

Read the pair as:

> **`dependent` is completed only after `prerequisite` is completed.**

Equivalently, "`prerequisite` is **required by** `dependent`."

The Phase 1 chain, expressed this way:

```
payment-api  <-required by-  backend-integration
             <-required by-  integration-testing
             <-required by-  release-validation
             <-required by-  production-release
```

The committed `dependencies` list order is **not** guaranteed to be
topological — `get_plan_state` performs no sorting and consumers must not rely
on list order. The Phase 2 impact engine (§4) does its own BFS ordering and is
deterministic.

`frontend-integration` has **no** dependency edges to or from the payment-API
chain; it is intentionally independent.

---

## 4. Disruption simulation schema (Phase 2)

Operation: `simulate_disruption(scenario_id, entity_id, reason, severity="high")`.
Hypothetical and deterministic: the committed plan is never modified and its
`version` is never incremented.

### Disruption input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan"` |
| `entity_id` | existing entity id (the disrupted target) | yes | `"payment-api"` |
| `reason` | non-empty string | yes | `"service outage"` |
| `severity` | enum: `low` \| `medium` \| `high` \| `critical` (default `high`) | no | `"high"` |

### Impact result

| Field | Type | Example |
|-------|------|---------|
| `scenario_id` / `plan_id` | echoed from the scenario | `"demo-release-plan"` |
| `plan_version` | committed version, unchanged by simulation | `1` |
| `simulated` | always `true` (flags hypothetical output) | `true` |
| `disruption` | object: `entity_id`, `name`, `state` (`blocked`), `reason`, `severity` | see below |
| `directly_affected` | list of affected entries one hop from the target | see below |
| `transitively_affected` | list of affected entries two or more hops away | see below |
| `unaffected` | list of `{entity_id, name, state}` where `state` is the committed status | see below |

Each **affected entry** has:

| Field | Type | Example |
|-------|------|---------|
| `entity_id` | string | `"integration-testing"` |
| `name` | string | `"Integration testing"` |
| `state` | always `blocked` (existing entity status) | `"blocked"` |
| `evidence_path` | list of entity ids from the target to this entity | `["payment-api", "backend-integration", "integration-testing"]` |

Example (target `payment-api`):

```json
{
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "plan_version": 1,
  "simulated": true,
  "disruption": {"entity_id": "payment-api", "name": "Payment API service",
                 "state": "blocked", "reason": "service outage", "severity": "high"},
  "directly_affected": [
    {"entity_id": "backend-integration", "name": "Backend integration",
     "state": "blocked", "evidence_path": ["payment-api", "backend-integration"]}
  ],
  "transitively_affected": [
    {"entity_id": "integration-testing", "name": "Integration testing",
     "state": "blocked",
     "evidence_path": ["payment-api", "backend-integration", "integration-testing"]}
  ],
  "unaffected": [
    {"entity_id": "frontend-integration", "name": "Frontend integration",
     "state": "in_progress"}
  ]
}
```

### Impact rules (normative)

- Propagation follows **dependent** edges from the target: everything that
  (transitively) requires an unavailable entity cannot proceed.
- **Direct** = one hop; **transitive** = two or more hops.
- Impacted entities are classified `blocked`. No new statuses are introduced.
- **Independent work stays healthy**: `frontend-integration` is in
  `unaffected` with its committed status, never `blocked`.
- Result lists follow scenario entity order; BFS sorts dependents, so output is
  deterministic for the same input.

### Errors

- Unknown `scenario_id` → `unknown scenario_id '<id>'; available: [...]`.
- Unknown `entity_id` → `unknown entity_id '<id>'; available: [...]`.
- Empty `reason` → `reason must be a non-empty string`.
- Unsupported `severity` → `unknown severity '<v>'; allowed: [...]`.

All are returned as a structured tool error (`is_error: true`); no fallback
value is ever substituted.

---

## 5. Recovery alternatives schema (Phase 3)

Operation: `find_alternative_dependencies(scenario_id, entity_id)`.
Hypothetical and deterministic: the committed plan, its dependencies, entity
statuses, and `version` are never modified.

### Semantics (chosen interpretation — see ambiguity note)

An **alternative dependency** here means a *modified view of the existing
dependency set* in which the broken edge is removed. No new graph and no new
entities are introduced. For each dependency edge whose `prerequisite` equals
the disrupted `entity_id` (processed in fixture order), one option is produced
proposing to bypass that edge.

> **Ambiguity flagged:** the contract previously had no concept of an
> "alternative dependency". This slice defines it as edge removal. If the team
> intends *substitution* (replacing the prerequisite with a different existing
> entity), that is a different contract and must be agreed separately.

### Recovery input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan"` |
| `entity_id` | existing entity id (the disrupted prerequisite) | yes | `"payment-api"` |

### Recovery result

| Field | Type | Example |
|-------|------|---------|
| `scenario_id` / `plan_id` | echoed from the scenario | `"demo-release-plan"` |
| `plan_version` | committed version, unchanged | `1` |
| `simulated` | always `true` | `true` |
| `disruption` | `{entity_id, name}` of the disrupted prerequisite | see below |
| `options` | list of recovery options (possibly empty) | see below |

Each **recovery option**:

| Field | Type | Example |
|-------|------|---------|
| `option_id` | stable id `drop-dependency:{dependent}->{prerequisite}` | `"drop-dependency:backend-integration->payment-api"` |
| `description` | human-readable summary | `"Bypass the dependency 'backend-integration' requires 'payment-api' ..."` |
| `affected_entity_id` | the dependent entity | `"backend-integration"` |
| `affected_dependency` | the edge being bypassed (existing shape) | `{"dependent": "backend-integration", "prerequisite": "payment-api"}` |
| `alternative` | `{action, removed_dependency, dependencies}` | see below |
| `unblocks` | entity ids freed, in scenario order | `["backend-integration", ...]` |
| `assumptions` | list of strings | see below |
| `limitations` | list of strings | see below |

`alternative.action` is the string `"remove_dependency"`. `alternative.removed_dependency`
equals `affected_dependency`. `alternative.dependencies` is the committed
dependency list (existing shape) with the removed edge omitted — proposed,
not applied.

Example (target `payment-api`):

```json
{
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "plan_version": 1,
  "simulated": true,
  "disruption": {"entity_id": "payment-api", "name": "Payment API service"},
  "options": [
    {
      "option_id": "drop-dependency:backend-integration->payment-api",
      "description": "Bypass the dependency 'backend-integration' requires 'payment-api' so 'backend-integration' can proceed without the disrupted prerequisite.",
      "affected_entity_id": "backend-integration",
      "affected_dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
      "alternative": {
        "action": "remove_dependency",
        "removed_dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
        "dependencies": [
          {"dependent": "integration-testing", "prerequisite": "backend-integration"},
          {"dependent": "release-validation", "prerequisite": "integration-testing"},
          {"dependent": "production-release", "prerequisite": "release-validation"}
        ]
      },
      "unblocks": ["backend-integration", "integration-testing", "release-validation", "production-release"],
      "assumptions": ["Assumes 'backend-integration' does not strictly require 'payment-api' for this release."],
      "limitations": ["Hypothetical only; the committed plan is not changed."]
    }
  ]
}
```

(Example abridged: `assumptions`/`limitations` lists are shortened.)

### Recovery rules (normative)

- Options are produced only for **direct** dependency edges on the disrupted
  entity; options follow fixture dependency order — deterministic for identical
  input.
- **`unblocks` is computed on the residual graph.** For a proposed removal of
  edge `dependent -> entity_id`, build the committed dependencies with that one
  edge removed. `unblocks` is every entity affected by the disruption in the
  committed graph that is **no longer reachable** from the disrupted `entity_id`
  in the residual graph, listed in scenario entity order. It is a structural
  claim ("the disruption's dependency path to this entity has been removed"),
  not an inference from reachability in the original graph. An entity that still
  depends on the disrupted prerequisite through another path, or on another
  prerequisite that is itself still blocked, is deliberately **not** listed.
- A valid entity with no dependents yields `options: []` (a legitimate result,
  not a fallback).
- Independent work (`frontend-integration`) has no edge to the payment chain and
  therefore never appears in `affected_entity_id` or `unblocks`.
- The engine simulates a **single** disruption. `unblocks` treats the disrupted
  entity as the only unavailable one; with simultaneous disruptions it would
  overstate readiness.

### Errors

- Unknown `scenario_id` → `unknown scenario_id '<id>'; available: [...]`.
- Unknown `entity_id` → `unknown entity_id '<id>'; available: [...]`.

Returned as a structured tool error (`is_error: true`); no fallback.

---

## 6. Recovery-proposal evaluation schema (Phase 4)

Operation: `evaluate_recovery_plan(scenario_id, option_id, expected_plan_version)`.
Read-only and deterministic: the committed plan, its dependencies, entity
statuses, and `version` are never modified.

### Structural validity vs business readiness (normative)

The `verdict` answers a **structural** question only: does the proposal's
dependency edge exist, do its entity references resolve, and does the caller's
`expected_plan_version` match the committed version? It does **not** assert that
bypassing a prerequisite is safe or ready to execute — the scenario contains no
information about integration contracts, substitute services, or delivery
constraints. Every report therefore includes a
`business_readiness_not_assessed` **warning** finding and matching
`limitations`. A `structurally_valid` verdict must never be presented as a
verified fix.

### Evaluation input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan"` |
| `option_id` | a recovery option id, `drop-dependency:{dependent}->{prerequisite}` (§5) | yes | `"drop-dependency:backend-integration->payment-api"` |
| `expected_plan_version` | integer — the version the proposal was built against | yes | `1` |

> **Ambiguity flagged:** no proposal schema existed before Phase 4. This slice
> reuses the Phase 3 `option_id` as the proposal handle (parsed to the edge) and
> treats the disrupted entity as the option's `prerequisite`. A future proposal
> object may pass `{dependent, prerequisite}` explicitly; that is a different
> contract and must be agreed separately.

### Evaluation result

| Field | Type | Example |
|-------|------|---------|
| `evaluation_id` | `eval:{scenario_id}:{option_id}:v{expected_plan_version}` | see below |
| `scenario_id` / `plan_id` | echoed from the scenario | `"demo-release-plan"` |
| `plan_version` | committed version that was evaluated | `1` |
| `expected_plan_version` | echoed from the caller | `1` |
| `verdict` | enum: `structurally_valid` \| `rejected` | `"structurally_valid"` |
| `dependency` | the parsed edge `{dependent, prerequisite}` | see below |
| `findings` | list of `{code, level, message}` | see below |
| `impact` | object, or `null` when rejected | see below |
| `assumptions` | list of strings | see below |
| `limitations` | list of strings | see below |

`verdict` vocabulary (complete):

- **`structurally_valid`** — version matches, entity references resolve, and the
  edge exists. Includes a `business_readiness_not_assessed` warning.
- **`rejected`** — a structural check failed (stale version, unknown entity, or
  missing edge). `impact` is `null`.

`findings[].level` vocabulary: `info`, `warning`, `error`. Codes emitted today:
`plan_version_match`, `stale_plan_version`, `dependency_edge_exists`,
`missing_dependency`, `unknown_entity`, `business_readiness_not_assessed`.
Findings are emitted in a fixed order, so output is deterministic.

`impact` (populated only when `verdict` is `structurally_valid`):

| Field | Type | Example |
|-------|------|---------|
| `disrupted_entity_id` | the option's `prerequisite` | `"payment-api"` |
| `removed_dependency` | the edge `{dependent, prerequisite}` | see below |
| `unblocks` | entity ids no longer reachable from the prerequisite in the **residual** graph (§5 residual semantics) | `["backend-integration", ...]` |
| `still_blocked` | entity ids still reachable in the residual graph | `[]` |

Example (valid proposal, target `payment-api`):

```json
{
  "evaluation_id": "eval:demo-release-plan:drop-dependency:backend-integration->payment-api:v1",
  "scenario_id": "demo-release-plan",
  "plan_id": "plan-payment-api-release",
  "plan_version": 1,
  "expected_plan_version": 1,
  "verdict": "structurally_valid",
  "dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
  "findings": [
    {"code": "plan_version_match", "level": "info",
     "message": "expected_plan_version 1 matches the committed plan version 1."},
    {"code": "dependency_edge_exists", "level": "info",
     "message": "dependency edge 'backend-integration' requires 'payment-api' exists in the committed plan."},
    {"code": "business_readiness_not_assessed", "level": "warning",
     "message": "Structural validity only: business readiness ... is not assessed and must be reviewed by a human."}
  ],
  "impact": {
    "disrupted_entity_id": "payment-api",
    "removed_dependency": {"dependent": "backend-integration", "prerequisite": "payment-api"},
    "unblocks": ["backend-integration", "integration-testing", "release-validation", "production-release"],
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

### Evaluation rules (normative)

- A proposal that fails any structural check returns `verdict: "rejected"` and
  `impact: null` — a **documented rejection result**, never a silent fallback to
  a different plan, edge, or version.
- On branching graphs, `unblocks`/`still_blocked` are computed on the residual
  graph (§5). Removing one prerequisite of a multi-prerequisite dependent does
  **not** list that dependent as unblocked while another of its prerequisites
  remains reachable.
- Evaluation never mutates the scenario, its entities, dependencies, or version.

### Errors vs rejection

- Unknown `scenario_id` → structured error (`is_error: true`), same as §4/§5.
- Malformed `option_id` or non-integer `expected_plan_version` → structured
  error (the request itself is malformed).
- Well-formed proposals that fail a check (stale version, unknown entity,
  missing edge) → `rejected` report (`is_error: false`).

---

## 7. Workload rebalancing schema (Phase 5)

Operation: `rebalance_workload(scenario_id, expected_plan_version, resource_id=None)`.
Hypothetical and deterministic: the committed plan, its resources, assignments,
dependencies, entity statuses, and `version` are never modified.

> **Phase 5 introduces two optional plan fields**, `resources` (scenario-level)
> and `assignee`/`effort` (entity-level). They are **optional**: scenarios without
> them remain valid and simply return `outcome: "insufficient_data"`. The values
> in the shipped `demo-release-plan-staffed` fixture are **synthetic demo data**,
> not real capacity, effort, or availability.

### Resource schema

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `id` | string, stable, non-empty, unique within a scenario | yes | `"alice"` |
| `name` | string (display only) | yes | `"Alice (synthetic)"` |
| `capacity` | non-negative integer, abstract `work_units` (`bool` excluded) | yes | `3` |

### Workload input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan-staffed"` |
| `expected_plan_version` | integer; must equal the committed `version` | yes | `1` |
| `resource_id` | existing resource id; scopes which resource's overload is addressed | no | `"alice"` |

### Workload result

| Field | Type | Example |
|-------|------|---------|
| `scenario_id` / `plan_id` | echoed from the scenario | `"demo-release-plan-staffed"` |
| `plan_version` | committed version | `1` |
| `expected_plan_version` | echoed from the caller | `1` |
| `simulated` | always `true` | `true` |
| `unit` | always the string `"work_units"` | `"work_units"` |
| `outcome` | enum: `ok` \| `insufficient_data` | `"ok"` |
| `resources` | per-resource summary (see below) | see below |
| `suggestions` | list of hypothetical moves | see below |
| `unresolved` | tasks that could not be moved | see below |
| `assumptions` | list of strings | see below |
| `limitations` | list of strings | see below |

Per-**resource** summary:

| Field | Type | Example |
|-------|------|---------|
| `resource_id` | resource id | `"alice"` |
| `name` | display name | `"Alice (synthetic)"` |
| `capacity` | declared capacity | `3` |
| `assigned_effort` | sum of **unfinished** assigned effort | `4` |
| `free_capacity` | `capacity - assigned_effort` (may be negative) | `-1` |
| `overloaded` | `true` when `free_capacity < 0` | `true` |
| `assigned_entity_ids` | unfinished entity ids, scenario order | `["release-validation", "production-release"]` |

Each **suggestion**:

| Field | Type | Example |
|-------|------|---------|
| `suggestion_id` | stable id `rebalance:{entity_id}->{resource_id}` | `"rebalance:production-release->bob"` |
| `entity_id` | the task being moved | `"production-release"` |
| `effort` | the task's effort in `work_units` | `1` |
| `from_resource_id` | current (overloaded) resource | `"alice"` |
| `to_resource_id` | destination resource | `"bob"` |
| `reason` | human-readable rationale | `"..."` |
| `assumptions` | list of strings | see below |
| `limitations` | list of strings | see below |

Each **unresolved** item: `{entity_id, resource_id, effort, reason}`.

Example (verified, abridged only by whitespace) is in the
[README §12](../README.md#12-example-rebalance_workload-call-and-response).

### Rebalancing rules (normative)

- A task counts toward a resource's load only when its `status` is **unfinished**
  (`pending`/`in_progress`/`blocked`) and it has both `assignee` and `effort`.
  `done` work is excluded.
- A resource is **overloaded** when its unfinished assigned effort exceeds its
  `capacity`. Workload does not depend on dependencies or the disrupted state —
  it is a raw capacity comparison.
- Suggestions are produced **greedily in scenario order**: for each overloaded
  source (in declared resource order), consider its unfinished entities in
  scenario order and place each on the **first** resource (declared order, not the
  source) with `free_capacity >= effort`. This may relocate more than the strict
  minimum; that is a stated limitation.
- A task that cannot be placed on any resource with enough free capacity remains
  in `unresolved` and is **not** removed from its origin.
- When `resource_id` is supplied, only that resource's overload is addressed;
  every other resource may still be a destination.
- The tool never mutates anything and never predicts a delivery date.

### Insufficient data vs errors

- No `resources` list → `outcome: "insufficient_data"` (`is_error: false`), with
  an explicit limitation. This is **not** a silent empty `ok`.
- Unknown `scenario_id` / unknown `resource_id` → structured error
  (`is_error: true`).
- Stale or non-integer `expected_plan_version` → structured error
  (`is_error: true`). *(Note: unlike §6 `evaluate_recovery_plan`, which returns a
  `rejected` report for a stale version, §7 rejects a stale version as an error.)*

---

## 8. Blocker triage schema (Phase 6)

Operation:
`analyze_blocked_tasks(scenario_id, entity_id, event_type="service_outage", reason=None, delay_minutes=None)`.
Hypothetical and deterministic: the committed plan, its entities, dependencies,
and `version` are never modified.

### Triage input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"project-full"` |
| `entity_id` | existing entity id (the disrupted target) | yes | `"payment-api"` |
| `event_type` | enum: `service_outage` \| `resource_unavailable` \| `delivery_delay` \| `transport_delay` (default `service_outage`) | no | `"service_outage"` |
| `reason` | non-empty string | no | `"service outage"` |
| `delay_minutes` | positive number; **required** for `delivery_delay`/`transport_delay` | conditional | `120` |

Event types: `service_outage` and `resource_unavailable` are **unbounded** — every
hard-reachable entity is blocked. `delivery_delay` and `transport_delay` carry a
`delay_minutes` value that propagates in topological order and can be **absorbed**
by downstream `metadata.slack_minutes` (see rules).

### Triage result

| Field | Type | Example |
|-------|------|---------|
| `scenario_id` / `plan_id` | echoed from the scenario | `"project-full"` |
| `plan_version` | committed version, unchanged | `1` |
| `simulated` | always `true` | `true` |
| `disruption` | `{entity_id, name, event_type, reason, delay_minutes}` | see below |
| `states` | one entry per entity (see below) | see below |
| `summary` | count per state: `{done, blocked, external, at_risk, healthy}` | see below |
| `parallel_work` | `{entity_id, name}` for every non-blocked, non-done entity | see below |
| `critical_path` | critical path object (§9) | see §9 |
| `critical_path_change` | critical-path-change object (§9) | see §9 |
| `assumptions` | list of strings | see below |
| `limitations` | list of strings | see below |

Each **state** entry:

| Field | Type | Example |
|-------|------|---------|
| `entity_id` | string | `"backend-integration"` |
| `name` | string | `"Backend integration"` |
| `state` | enum: `done` \| `blocked` \| `external` \| `at_risk` \| `healthy` | `"blocked"` |
| `reason` | human-readable rationale | `"Hard dependency path from 'payment-api' reaches ..."` |
| `evidence_path` | list of entity ids from the disruption to this entity (empty for `done`/`healthy`) | `["payment-api", "backend-integration"]` |
| `evidence_edges` | list of `{dependent, prerequisite, dependency_type}` connecting the path | see below |

### State semantics (normative)

Precedence for the single state: **`done` > `blocked`/`external` > `at_risk` >
`healthy`**.

- **`done`** — committed `status == "done"`. Reported `done` and never re-labelled
  blocked. A `done` entity does **not** stop propagation: downstream blockers
  still propagate through it (its completion is not treated as a buffer).
- **`blocked`** — a hard (`requires_completion` / `requires_arrival_by`) path
  leads from the disrupted target to this entity. The disrupted target itself is
  always `blocked` (or `external`), overriding its committed status.
- **`external`** — `blocked`, but at least one entity on the evidence path is
  flagged `metadata.external`, i.e. the blocker is outside the team's control.
- **`at_risk`** — not blocked, but: it is a soft (`benefits_from`) successor of
  something on the disruption path, **or** a delay reaches it but is absorbed by
  its slack (see below), **or** its declared `metadata.slack_minutes` is at or
  below the at-risk threshold.
- **`healthy`** — no dependency path links it to the disruption.

**Delay absorption** (`delivery_delay` / `transport_delay`): the delay
propagates along hard edges. At each node the arriving delay minus that node's
`metadata.slack_minutes` (`0` when unset) is passed on; the node is blocked only
while the arriving delay **exceeds** its slack. A node that receives a delay but
absorbs it becomes `at_risk`, and a downstream node reached only through an
absorbing buffer is **not** assumed to fail.

**At-risk threshold.** A declared `slack_minutes` at or below the fixed
`AT_RISK_SLACK_MINUTES = 60.0` minutes makes the entity `at_risk`.

Example (verified, abridged only by whitespace) is in the
[README §13](../README.md#13-example-analyze_blocked_tasks-call-and-response-blocker-triage).

### Recovery view (`recompute_with_changes`)

`rippleback.triage.recompute_with_changes(scenario, entity_id, changes, ...)`
re-runs triage on an in-memory modified plan and adds a top-level `state_changes`
list (`{entity_id, from, to}`). `changes` is a list of operations that
`apply_changes` applies to a copy: removing a dependency, adding a dependency, or
replacing the dependency list. This is how a Person 3 recovery option (e.g.
removing the broken edge) is shown to unblock work, **without** touching the
committed plan or `version`.

### Errors

- Unknown `scenario_id` / `entity_id` → `unknown ...; available: [...]`.
- Unsupported `event_type` → `unsupported event_type '<v>'; supported: [...]`.
- `delivery_delay`/`transport_delay` without a positive `delay_minutes` →
  `event_type '<v>' requires a positive delay_minutes` (code
  `INVALID_DISRUPTION`).
- A malformed/cyclic graph → `GraphError` code (§9).

All are returned as a structured tool error (`is_error: true`); no fallback.

---

## 9. Typed dependency graph and critical path (Phase 6)

### Graph

`rippleback.graph.build_graph(scenario)` builds a directed, typed NetworkX graph
`G = (V, E)`, edges prerequisite → dependent. Node attributes: `name`, `status`,
`duration`, `external`, `slack_minutes`, `metadata`. Edge attributes:
`dependency_type`, `hard`, `constraint_parameters`.

Validation raises `GraphError(code, message)` with one of the stable codes:

| Code | Meaning |
|------|---------|
| `ENTITY_NOT_FOUND` | an edge references an unknown entity id |
| `INVALID_DEPENDENCY` | a self-dependency or a malformed edge |
| `INVALID_DEPENDENCY_TYPE` | an unknown `dependency_type` |
| `CYCLIC_DEPENDENCY` | a cycle over hard edges |

`graph_errors(scenario)` returns the same faults as coded dicts without raising;
`validate_scenario()` also runs cycle detection so `get_plan_state` refuses a
cyclic fixture.

### Critical path

`rippleback.critical_path.critical_path(scenario)` returns:

| Field | Type | Example |
|-------|------|---------|
| `path` | list of entity ids, longest hard chain | `["payment-api", "backend-integration", ...]` |
| `length` | sum of node durations, or number of steps | `10` |
| `unit` | `"duration_units"` when any entity declares `duration`, else `"steps"` | `"duration_units"` |
| `edges` | the `{dependent, prerequisite, dependency_type}` chain | see below |

`critical_path_changes(scenario, entity_id)` compares the baseline critical path
with the longest hard chain inside the disruption's impact set, and reports the
disrupted entity and whether it is external. The `length` is a **relative
ranking** of dependency chains, never a delivery date or a duration forecast.

---

## 10. Delivery forecast schema (Milestone 6)

Operation: `recalculate_delivery_forecast(scenario_id, changes=None,
option_id=None, disrupted_entity_id=None)`. Engine:
`rippleback.forecast.recalculate_delivery_forecast(scenario, *,
disrupted_entity_id=None, changes=None, option_id=None)`. Read-only and
deterministic: the committed plan is never modified.

### Forecast input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan"` |
| `changes` | list of `apply_changes` operations (`remove_dependency` / `add_dependency`) | one of `changes`/`option_id`/neither | see below |
| `option_id` | legacy `drop-dependency:{dependent}->{prerequisite}` id (§5) | one of `changes`/`option_id`/neither | `"drop-dependency:backend-integration->payment-api"` |
| `disrupted_entity_id` | existing entity id | no | `"payment-api"` |

Providing both `changes` and `option_id` is ambiguous and is an error.
`substitute:{prerequisite}->{substitute}` ids (§14) are **not parseable** here
and are rejected with a structured error directing the caller to pass the
option's explicit `changes`.

### Forecast result

Keys, in order: `scenario_id`, `plan_id`, `plan_version`, `simulated`,
`disrupted_entity_id`, `option_id`, `changes`, `feasible`, `error`, `unit`,
`baseline_path`, `baseline_length`, `baseline_edges`, `forecast_path`,
`forecast_length`, `forecast_edges`, `delay_delta`, `assumptions`,
`limitations`.

| Field | Type | Meaning |
|-------|------|---------|
| `unit` | `"steps"` \| `"duration_units"` | from §9 critical path (durations when any entity declares `duration`) |
| `baseline_*` | critical-path report for the committed plan | §9 shape |
| `forecast_*` | critical-path report after `changes`, or `null` when infeasible | §9 shape |
| `delay_delta` | `forecast_length - baseline_length`, or `null` when infeasible | a **difference of relative totals**, never a duration and never a date |
| `feasible` / `error` | `true`/`null`, or `false`/`{code, message}` | in-band invalid-graph outcome |

### Forecast rules (normative)

- Lengths are relative critical-path durations in `unit` — **never calendar
  dates**; the repository models no start date, calendar, or working hours.
- A well-formed change set that yields an invalid graph (cycle, unknown
  reference, self-dependency) returns `feasible: false` plus the stable §9
  graph error code and `null` forecast fields — an invalid plan is never
  reported as a valid forecast, and this is a result, not an error.
- Invalid *requests* (unknown scenario/entity, non-list or malformed `changes`,
  malformed/unknown `option_id`, both inputs given) are structured errors
  (`is_error: true`).
- Changes apply with `rippleback.triage.apply_changes` semantics on a copy;
  the committed plan and `version` are unchanged.

---

## 11. Hard-constraint validation schema (Milestone 6)

Operation: `validate_hard_constraints(scenario_id, changes=None)`. Engine:
`rippleback.constraints.validate_hard_constraints(scenario, *, changes=None)`.
Read-only and deterministic.

### Validation input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan"` |
| `changes` | list of `apply_changes` operations; `null` validates the committed plan (baseline) | no | see §10 |

### Validation result

Keys, in order: `scenario_id`, `plan_id`, `plan_version`, `simulated`,
`changes`, `feasible`, `graph_errors`, `constraints`, `violations`,
`assumptions`, `limitations`.

Each `violations` entry: `{code, message, source, constraint_index,
constraint_type, entity_ids}` where `source` is `"graph"` or `"constraint"`.
Graph codes come first (§9: `ENTITY_NOT_FOUND`, `INVALID_DEPENDENCY`,
`INVALID_DEPENDENCY_TYPE`, `CYCLIC_DEPENDENCY`), then committed constraints in
fixture order (`CONSTRAINT_VIOLATED`, `UNSUPPORTED_CONSTRAINT`).

### Validation rules (normative)

- Supported constraint type: **`all_entities_done_before`**, checked as a
  **current-state invariant** — a listed gate entity must not be `done` while
  any other entity in the scenario is not `done`. `satisfied` means no violation
  exists in the current committed state, not a guarantee about future
  transitions. Change sets modify dependencies only and can never change a
  status.
- Unsupported constraint types are reported as violations and make the report
  infeasible; they are never silently skipped or assumed satisfied.
- `feasible: true` means every graph rule passed **and** every constraint was
  supported and currently satisfied.
- No schedule or calendar is modeled: "done before" is evaluated from committed
  statuses and dependency structure, never from wall-clock time.
- Malformed scenarios, non-list `changes`, and unsupported change actions are
  structured errors (`ConstraintError` → tool error); a well-formed but invalid
  proposal is an in-band `feasible: false` report.

---

## 12. Scoring and ranking schema (Milestone 6)

Engine: `rippleback.scoring.rank_options(scenario, options, weights=None)` and
`rippleback.scoring.score_option(metrics, weights=None)`. Read-only and
deterministic; lower scores win.

### Score formula (normative)

```
score = w_delay·delay + w_cost·cost + w_risk·risk
      + w_strain·resource_strain + w_count·disruption_count
```

| Metric | Default weight | Source |
|--------|----------------|--------|
| `delay` | `1.0` | derived from §10 `delay_delta` (relative critical-path units) |
| `cost` | `0.0` | **no source in the repository**; caller-supplied `option["metrics"]` only |
| `risk` | `0.0` | **no source in the repository**; caller-supplied only |
| `resource_strain` | `0.0` | derived from §7 workload overload when `resources` exist (scenario-level, identical for every option) |
| `disruption_count` | `0.0` | caller-supplied only |

### Ranking rules (normative)

- Each option needs a unique non-empty `option_id`. Its change set is
  `option["changes"]` when present (this is how §14 substitute options flow
  through), otherwise derived from a `drop-dependency:` id that must match a
  committed edge — an id matching nothing is excluded as a no-op recovery.
- Feasibility is gated with §11 **before** any score is computed: infeasible
  options are excluded with their violations preserved, never scored, never
  recommended.
- **Missing-metric policy:** a metric with nonzero weight but no supplied value
  makes the option **unscorable** (excluded with an explanatory reason) — never
  scored with a fabricated zero. Equal scores tie-break by ascending `option_id`.
- Outcomes: `ranked` (≥1 scorable option) or `no_feasible_recovery`.
- Weights are unitless; scores are comparable only within one call. Malformed
  options/ids/metrics/weights raise `ScoringError` → tool error.

---

## 13. Option comparison schema (Milestone 6)

Operation: `compare_recovery_options(scenario_id, options, weights=None)`.
Engine: `rippleback.comparison.compare_recovery_options(scenario, options,
weights=None)`. Read-only and deterministic.

Returns the full §12 ranking report (same `outcome`, `weights`,
`feasible_count`, `scorable_count`, `recommendation`, `ranked`, `excluded`)
plus, inserted after `weights`: `baseline` (the committed plan's §9 critical-path
report) and, per ranked **and** excluded entry, `forecast` (a §10 forecast for
that option's change set, with `disrupted_entity_id` and `option_id` `null`) and
`no_op` (whether applying the option's change set leaves the committed
dependency plan unchanged, edge order ignored).

| Field | Type | Meaning |
|-------|------|---------|
| `recommendation` | option id \| `null` | top option that is both feasible and scorable, ties by ascending `option_id`; `null` with `outcome: "no_feasible_recovery"` |
| `excluded[].violations` | list (§11 shape) | preserved verbatim for infeasible options |
| `excluded[].reasons` | list of strings | why the option was gated out, unscorable, or a no-op |

### Comparison rules (normative)

- **Infeasible options are never recommended** — they appear only in
  `excluded`, with their violations.
- A no-op option can be feasible and scorable but is limited so it is never
  presented as an improvement; when the recommendation itself is a no-op, the
  report says to keep the baseline.
- `options` is a non-empty list of option objects (§5 recovery options and §14
  substitute options both flow through unchanged); malformed options, weights,
  or metrics are structured errors.
- `delay_delta` values are differences of relative totals (§10), never
  durations or dates.

---

## 14. Substitute recovery options schema (Milestone 6)

Operation: `find_substitute_options(scenario_id, entity_id)`. Engine:
`rippleback.recovery.find_substitute_options(scenario, *, entity_id)`.
Read-only and deterministic: the committed plan is never modified.

### Candidate convention (`metadata.substitutes`)

A substitute candidate exists **only** when the disrupted entity explicitly
declares `metadata.substitutes` — a list of entity ids in the same scenario
(§2). Duplicates are removed (first occurrence kept). Nothing is inferred from
names, statuses, resources, or graph structure, and no service, availability,
compatibility, cost, or risk is invented for a candidate. **No shipped fixture
declares `metadata.substitutes`**, so all seeded scenarios return
`no_substitute_candidates` with `missing_data` explaining what is absent.

### Substitute input

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `scenario_id` | existing scenario id | yes | `"demo-release-plan"` |
| `entity_id` | existing entity id (the disrupted prerequisite) | yes | `"payment-api"` |

### Substitute result

Keys, in order: `scenario_id`, `plan_id`, `plan_version`, `simulated`,
`disruption`, `outcome`, `options`, `missing_data`, `assumptions`,
`limitations`.

`outcome` (complete): **`substitutes_proposed`** (≥1 feasible option),
**`no_feasible_substitute`** (candidates declared, none feasible), or
**`no_substitute_candidates`** (no candidate data; see `missing_data`).

Each option (keys in order): `option_id`, `explanation`,
`disrupted_entity_id`, `substitute_entity_id`, `affected_entity_ids`,
`affected_dependencies`, `changes`, `feasible`, `reasons`, `violations`,
`assumptions`, `limitations`.

### Substitute rules (normative)

- `option_id` format: `substitute:{prerequisite}->{substitute}` — distinct from
  the §5 `drop-dependency:` ids; the evaluator (§6) and the §10 `option_id`
  parser accept only `drop-dependency:` ids, so substitute options travel
  through §13/§10 via their explicit `changes`.
- Each option's `changes` reroutes **every** dependency edge on the disrupted
  prerequisite: one `remove_dependency` plus one `add_dependency` per dependent
  (fixture dependency order), preserving each edge's `dependency_type` and
  `constraint_parameters`.
- Every candidate is gated with §11 (graph validation included). Options that
  fail the gate — or are structurally rejected (candidate equals the disrupted
  entity, or names an unknown entity) — are reported `feasible: false` with
  `reasons` and verbatim `violations` (`CYCLIC_DEPENDENCY`,
  `ENTITY_NOT_FOUND`, `INVALID_DEPENDENCY`, `CONSTRAINT_VIOLATED`, ...).
- Options follow declaration order with duplicates removed, so identical input
  yields identical output.
- A malformed `metadata.substitutes` value (not a list, or a non-string entry)
  and an unknown `entity_id` are structured errors (`is_error: true`).

### 14.1. Recovery Scheduling Engine (`scheduler.py`, `generate_recovery_schedule`)

A **relative-duration** scheduling engine for recovery scenarios. Given a
scenario, an optional recovery change set (the §3 `apply_changes` shape) or a
§5 legacy `option_id`, and an optional `disrupted_entity_id`, it reports,
purely and deterministically and without any calendar:

- **`schedule`** — one entry per entity (including blocked ones, for
  transparency), in plan order, each with:
  - `entity_id`, `name`, `status`, `triage_state` (when triage ran),
  - `group` — `completed` (`done` entities are complete at t=0 and are never
    restarted) | `ready_now` | `waiting` (an unfinished hard prerequisite is
    waiting or blocked) | `blocked` (committed `blocked`, or triage
    `blocked`/`external`),
  - `layer` — parallel-execution layer (1-based) for `ready_now` entries
    (else `null`),
  - `duration`, `earliest_start`, `earliest_finish`, `waiting_time` — all in
    the report's `unit`, **relative offsets from the moment of analysis**, and
    all `null` unless every entity declares a duration (below),
  - `hard_prerequisites` (from `requires_completion`/`requires_arrival_by`
    edges — the only ordering constraint), `soft_prerequisites`
    (`benefits_from` edges never constrain start times),
  - `on_critical_path`, `reason` (human-readable).
- **`groups`** — entity ids per group; **`execution_order`** — a
  deterministic topological order of the `ready_now` tasks (lexicographic
  tie-breaking within layers); **`parallel_groups`** — `{layer, entity_ids}`.
- **`critical_path`** — the existing §10 structural longest-hard-chain report,
  unchanged (it counts completed tasks and is not a time forecast).
- **`completion`** — `duration` (max earliest finish over schedulable
  unfinished work), `unit`, `covers_all_tasks`, `excluded_tasks` (blocked or
  waiting), `explanation`.

**Durations are never invented.** Durations come from entity `duration`
fields (or explicit caller overrides in `scheduling_config["durations"]`).
When any entity lacks a duration the report is ordering-only: `outcome =
"insufficient_duration_data"`, start/finish/completion are `null`, and
`missing_data` names the entities. `scheduling_config["duration_unit"]` is a
label; only when it is explicitly `"minutes"` are §11
`required_buffer_minutes` arrival buffers added to start times (otherwise they
are preserved, reported, and deliberately not mixed into unitless durations).

**Recovery comparison.** With a change set, the report contains `baseline`
(the committed plan's schedule under the same durations and the disruption's
pre-change triage states) and `comparison`: both execution orders and
critical paths, `completion_delta` **only when both sides have the same valid
unit and durations** (`comparable` + `incomparable_reason` otherwise),
per-task `task_offset_changes`, `newly_blocked`/`newly_unblocked`,
parallelism changes, and `baseline_excluded_tasks`/`proposed_excluded_tasks`
(so a delta over differing work sets is always visible).

**Infeasibility.** A proposal that creates a cycle, a dangling reference, or a
hard-constraint violation (the §11 validator is reused, not reimplemented) is
reported in-band: `feasible = false`, `outcome = "infeasible"`, `error =
{code, message}`, `violations` (stable §11 codes), and `null` schedule,
execution order, completion, baseline, and comparison — an infeasible proposal
is never ordered, ranked, or presented as a schedule.

**Non-goals (honest limits).** No calendar scheduling (no start date, timezone,
working days, hours, or holidays exist in the data — `t = 0` is the moment of
analysis and every reported time is a relative offset); no resource-constrained
scheduling (`work_units` are abstract capacity with no time dimension; parallel
groups assume unlimited parallel execution subject only to hard dependencies);
no deadline optimization or resource leveling. The engine never mutates the
committed plan, its statuses, or its version.

---

- Entity `id` values are the stable identifiers; `name` is display-only and
  may change.
- `scenario_id` and `plan_id` are stable for the lifetime of the scenario.
- `version` increments when a scenario's structure changes.
- **OPEN:** are ids rename-safe across teams/tools, or should entities get an
  opaque UUID alongside a human-readable id?

---

## Required fields and validation summary

Minimum for a scenario to be accepted:
`scenario_id`, `plan_id`, `version`, `entities`, `dependencies`, `constraints`.
`resources` (and entity `assignee`/`effort`) are **optional** (Phase 5). A
scenario with a missing field, a bad status, an unknown reference, a
self-dependency, or a duplicate id is rejected by `validate_scenario()` and
reported as a malformed-scenario error by `get_scenario()`. Wrongly typed
`resources`/`assignee`/`effort` values are likewise rejected.

Unknown `scenario_id` at tool time → `is_error: true`, message:
`unknown scenario_id '<id>'; available: ['demo-release-plan', 'demo-release-plan-staffed', 'project-full', 'travel']`

---

## Open questions for the team

1. **Dependency direction** — is `{dependent, prerequisite}` clear, or should
   we use an explicit `requires`/`required_by` pair, or `{from, to}`? This is
   the single most important convention to sign off on before graph work.
2. **Status enums** — are the entity statuses (`pending`/`in_progress`/`done`/
   `blocked`) sufficient? Do we need `skipped`/`at_risk`? Should the scenario
   `status` enum be `healthy`/`degraded`/`disrupted`, and what sets it?
3. **Stable IDs** — keep human-readable ids only, or add opaque UUIDs
   (`id` vs `key`)? Should ids be namespaced (`svc.payment-api`) to avoid
   collisions when merging scenarios?
4. **Constraints** — is `all_entities_done_before` the right minimal shape?
   What other constraint types are needed in Phase 2 (budget, date, capacity)?
   Are `entity_ids` plural by design?
5. **`disruptions`** — Phase 2 keeps the committed plan's `disruptions` at `[]`
   and models disruptions only inside `simulate_disruption` results (§4). Still
   OPEN: should confirmed (non-simulated) disruptions ever be persisted into the
   plan, and what extra fields (timestamp, owner, severity scale) would they
   need? `severity` uses `low`/`medium`/`high`/`critical` for now.
6. **`version` semantics** — increment on any edit, or only structural edits?
   Is `version` per-scenario or per-plan?
7. **Multiple scenarios** — will `plan_id` group several scenarios, or is
   one scenario == one plan?
8. **Mutation model** — Phase 1 is read-only. When writes are added, do we
   version-check (`version` optimistic concurrency) or last-write-wins?
9. **Recovery semantics** — Phase 3 defines an "alternative dependency" as
   *removing* the broken edge (§5). Is edge removal the intended recovery
   primitive, or should the team specify substitution candidates, cost/effort
   ranking, or multi-step recovery plans? Also: is `option_id` format
   `drop-dependency:{dependent}->{prerequisite}` acceptable as a stable id?
   **Update (Milestone 6):** §12–§14 now define ranking (delay-only defaults,
   no fabricated cost/risk) and substitution via an explicit
   `metadata.substitutes` declaration with `substitute:{prerequisite}->{substitute}`
   ids — open for team ratification, especially whether the substitute
   declaration should stay in freeform entity `metadata` or become a typed field.
10. **Evaluation semantics (Phase 4)** — is `structurally_valid` vs `rejected`
    the right verdict vocabulary, or do we need a third state? Should a failed
    structural check be a `rejected` report (current) or a structured error?
    Should business readiness ever be derivable from the scenario (requiring a
    substitute/defer field on entities), or stay a human decision? Is reusing
    `option_id` as the proposal handle acceptable, or should proposals pass
    `{dependent, prerequisite}` explicitly?
    **Update (Milestone 6):** business readiness stays a human decision —
    `find_substitute_options` surfaces declared candidates with feasibility
    limited to hard-constraint/graph validity (§14), never availability,
    compatibility, cost, or risk. The evaluator's verdict vocabulary is
    unchanged and still accepts only `drop-dependency:` ids.
 11. **Workload / capacity model (Phase 5)** — is a flat scenario-level
     `resources` list with per-entity `assignee`/`effort` the right minimal model?
     Should `effort` be integer `work_units` or carry a unit/duration? Should stale
     `expected_plan_version` be an **error** (current §7) or a `rejected`-style
     report (as in §6)? Is greedy scenario-order placement acceptable, or do we
     need a globally optimal assignment? Do we need per-entity skills/roles,
     availability windows, or partial moves? Is the `insufficient_data` outcome the
     right way to signal a non-staffed scenario?
12. **Cycle detection and graph types (Phase 6)** — are `CYCLIC_DEPENDENCY` and
     edge types (`requires_completion`/`requires_arrival_by`/`benefits_from`)
     sufficient? Should `requires_arrival_by` gain a typed `constraint_parameters`
     schema (currently freeform dict)?
13. **Blocker triage (Phase 6)** — is the 60-minute at-risk slack threshold
     correct? Should done entities ever block downstream (current: they do not stop
     propagation)? Is `metadata.external` the right flag for "outside the team"? Are
     event types sufficient, or should `severity` influence delay absorption?
