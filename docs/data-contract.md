# RippleBack data contract

Source of truth: `src/rippleback/scenarios.py` (plan data) and
`src/rippleback/impact.py` (disruption simulation). Plan validation is enforced
by `validate_scenario()` at fixture definition/lookup time and surfaced through
`get_plan_state`.

Phase 1 defined the plan schema (§1–§3). Phase 2 added hypothetical disruption
simulation (§4). Phase 3 adds hypothetical recovery alternatives (§5). Both
Phase 2 and Phase 3 are read-only and return separate result objects; neither
changes the plan schema.

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

Example:

```json
{ "id": "payment-api", "name": "Payment API service", "status": "done" }
```

---

## 3. Dependency schema

A **dependency** is a directed edge between two entities. It contains no state
of its own; it only expresses ordering.

| Field | Type | Required | Example |
|-------|------|----------|---------|
| `dependent` | entity id of the work that needs the other first | yes | `"backend-integration"` |
| `prerequisite` | entity id of the work that must come first | yes | `"payment-api"` |

Example:

```json
{ "dependent": "backend-integration", "prerequisite": "payment-api" }
```

Rules (enforced by `validate_scenario()`):

- Both values must reference an existing entity id in the same scenario.
- Self-dependency (`dependent == prerequisite`) is rejected.
- Duplicate `(dependent, prerequisite)` pairs are rejected.

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

## Stable IDs

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
A scenario with a missing field, a bad status, an unknown reference, a
self-dependency, or a duplicate id is rejected by `validate_scenario()` and
reported as a malformed-scenario error by `get_scenario()`.

Unknown `scenario_id` at tool time → `is_error: true`, message:
`unknown scenario_id '<id>'; available: ['demo-release-plan']` (no fallback).

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
