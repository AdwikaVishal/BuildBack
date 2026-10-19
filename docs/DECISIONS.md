# RippleBack design decisions

This file records the choices made while turning RippleBack into a
production-real application, so a reviewer can see *what* was decided and
*why*. It is a decision log, not a specification; source files remain the
source of truth.

## Persistence

- **One database, three roles.** The MCP server, the adapter, and the CLI all
  talk to the same database. SQLite is the default (`sqlite:///./rippleback.db`,
  WAL + `busy_timeout` + foreign keys enabled); any SQLAlchemy URL works.
- **Scenario resolution is layered.** `scenarios.get_scenario` reads the
  in-process fixtures only; `repository.get_scenario` reads fixtures first and
  falls back to the database, validating whichever it uses. Tests that
  monkeypatch the fixtures therefore keep working unchanged.
- **Own migration runner, not Alembic.** `db.MIGRATIONS` plus a
  `schema_migrations` table is enough for an embedded app with one deployable
  schema, and avoids a heavyweight dependency. `init_db()` runs at startup of
  the server and adapter.
- **No separate resources table.** Resources are part of a plan snapshot
  (they are versioned with the plan, exactly like entities and dependencies).
  A denormalized table would create a second source of truth to reconcile.

## The write path

- **Dry run first.** Every change set is diffed, feasibility-checked, and
  signed into an **HMAC confirm token** bound to
  `(scenario_id, expected_version, exact changes)`. The token carries an
  expiry (`RIPPLEBACK_CONFIRM_TOKEN_TTL_SECONDS`, default 300s).
- **Optimistic concurrency.** `expected_plan_version` must match the stored
  version or the write is rejected as stale (HTTP 409).
- **Idempotency by request hash.** A commit carries a required
  `idempotency_key`. The hash is computed from the **raw** request inputs, so a
  replay with the same key returns the stored result, while a key reused for a
  different request is rejected.
- **One transaction per write.** A plan version, a commit row, an audit row,
  and (when write-back is on) an outbox row are written together. `PlanVersion`
  is inserted (not merged) so a concurrent writer surfaces as an
  `IntegrityError` and is reported as "a concurrent commit advanced this plan".
- **Gated by one policy.** `security.write_guard_reason` is the single source
  of truth for "may this write proceed": writes must be enabled, and a
  non-loopback bind without an API key refuses writes outright. The MCP server
  tools and the adapter's `/api/mcp/tool` route both call it.
- **Disabled by default.** `RIPPLEBACK_WRITES_ENABLED` defaults to false, so an
  empty `.env` cannot mutate anything.

## Disruptions

- Recording a disruption sets the **plan** status to `disrupted` and the
  **entity** to `blocked` — unless the entity is already `done`, in which case
  it is left `done` (a finished task cannot become un-finished).
- The prior entity status and prior plan status are stored on the disruption
  row. Resolving restores them, but only if the entity is still `blocked` at
  resolution time (a later change wins over a stale snapshot).

## PM sync and the overlay

- **Local commits are overlays**, never data loss. A sync fetches the PM plan,
  re-applies every commit whose effect the PM does not yet contain, and stores
  the result as the next version.
- **Retirement is content-based.** A commit is retired to `synced_back` only
  when the imported data actually contains its effect (`commit.is_reflected`).
- **Version bumps are content-hash based.** An unchanged re-sync does not
  advance the version; snapshot hashes are normalized before comparison so
  cosmetic differences do not create spurious versions.
- **Outbox, not fire-and-forget.** Write-back payloads are queued and drained
  idempotently, with claim-by-status so the server and adapter can share one
  database without double-pushing. Failures retry up to `MAX_ATTEMPTS`, then the
  commit is marked `writeback_failed` and notified once.

## Scoring metrics

The scoring engine is **pure**: it scores only what it is given and treats a
nonzero weight with a missing value as *unscorable*. `rippleback.metrics`
supplies real inputs and resolves weights safely.

- **Weight resolution policy.** An env weight (`SCORE_WEIGHT_*`) always wins. A
  metric auto-enables at 1.0 only when its source is configured **and every
  option has a computed value**. Otherwise it stays at the historical default
  (`delay=1.0`, everything else `0.0`), so a pristine demo is never unscorable.
  Reported per metric as a source label: `default | env | data |
  disabled_no_source | disabled_incomplete_data | disabled_opt_in` (plus
  `caller` when the caller supplies weights directly).
- **`resource_strain` is opt-in (env only)** because capacity data is
  caller/demo supplied; it is never auto-enabled from stored data.
- **Cost.** `COST_PER_CHANGE * len(changes)` plus the rework cost of every
  affected, unfinished entity: `metadata.cost` when a PM field supplies it,
  otherwise `effort * COST_RATE_PER_WORK_UNIT`. If any affected unfinished
  entity has no computable cost, the metric is **omitted** — never
  zero-filled.
- **Risk.** `sum(severity_weight * 0.5 ** (age_days / 30))` over incidents and
  open disruptions inside the option's blast radius. Recency is measured
  against the **newest recorded event**, not wall-clock time, so identical data
  always scores identically. Severity weights: low=1, medium=2, high=3,
  critical=4, unknown=1.
- **Disruption count.** Open disruptions inside the same blast radius.
- **Blast radius.** Directly changed entities (both ends of a dependency
  change, the entity of a reassignment) plus every transitive dependent.

Metric keys are `delay, cost, risk, resource_strain, disruption_count` and must
match `scoring.METRIC_KEYS` exactly (an earlier mismatch between `strain` and
`resource_strain` produced `ScoringError: unknown weight metric(s)`).

## Assistant safety

The conversational assistant is read-only. `assistant.providers.NON_READ_ONLY_TOOLS`
lists the five write tools; they are filtered out of the model's tool catalog
and refused by the orchestrator's tool runner even if a model asks for them
(defense in depth). Commits happen only through the dashboard's dry-run →
confirm → commit flow.

## Adapter middleware

The API-key gate is a **pure ASGI middleware**, not Starlette's
`BaseHTTPMiddleware`. The latter wraps downstream requests in its own anyio
task, which corrupts the cancel scopes used by the MCP SDK client and surfaces
as intermittent 503s (`RuntimeError: Attempted to exit a cancel scope that
isn't the current task's current cancel scope`).

## Out-of-scope dependency links

A PM "blocks" link can reference an issue outside the imported project. The
default (`RIPPLEBACK_OUT_OF_SCOPE_LINKS=stub`) keeps a clearly-labeled stub
entity so the dependency is not silently dropped; `=error` rejects the import
instead. The choice is reported in both the import result and the sync audit.

## Secrets

Credential values live only in the backend environment. They are never returned
over HTTP, written to the audit log, or placed in the browser; the adapter
reports configuration as **names only**. `security.redact` /
`redact_mapping` scrub both configured secret values and secret-shaped strings
(bearer tokens, AWS keys, GitHub/Slack tokens, PEM blocks) before anything is
logged, stored, or returned.
