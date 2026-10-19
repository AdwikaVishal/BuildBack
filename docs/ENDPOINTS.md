# RippleBack — Complete Endpoint Map

## The big picture (3 hops, 1 database)

```
[Browser]  ──REST /api/*──►  [Adapter :8001]  ──MCP /mcp──►  [MCP server :8000]
                                   │                              │
                                   └──────── shared DB ───────────┘
                                       (RIPPLEBACK_DB_URL)
```

- The browser **never** speaks MCP. It only calls the adapter's REST routes.
- The adapter is the **only** MCP client. It talks to the server over one protocol endpoint.
- The server exposes **no REST** — its 17 tools are dispatched over the single `/mcp` endpoint.
- Both backend processes read/write the **same database**.

---

## 1. Adapter REST endpoints (`:8001`) — what the browser calls

| # | Endpoint | Method | What it does | Connected to |
| - | --- | --- | --- | --- |
| 1 | `/api/health` | GET | Adapter + MCP connection status, discovered tool names, assistant provider | MCP server `/mcp` (health + tools/list) |
| 2 | `/api/status` | GET | Config/integration report (**names only, never secrets**) + scenario list (`scenarios.items`) | DB (`list_scenarios`) |
| 3 | `/api/mcp/tools` | GET | Live tool list with descriptions + JSON schemas (`?refresh=1` bypasses cache) | MCP server `/mcp` (tools/list) |
| 4 | `/api/mcp/tool` | POST | `{tool, arguments}` → structured result or clear error — **the main data path for every graph/impact/recovery/timeline call** | MCP server `/mcp` (tools/call) |
| 5 | `/api/assistant/chat` | POST | `{message, scenario_id?}` → assistant reply + full tool-call trace | Adapter's assistant → MCP server for read tools |
| 6 | `/api/sync` | POST | Run one PM import + PagerDuty ingest pass now | Jira/Linear/GitHub + PagerDuty, then DB |
| 7 | `/api/substitutes/import` | POST | Import a substitute-catalog file into the DB | DB |
| 8 | `/api/commits` | GET | Recent committed recoveries with diff + push state | DB |
| 9 | `/api/commits` | POST | Commit a confirmed change set (write-gated) | DB (transaction) + outbox → PM write-back |
| 10 | `/api/commits/dry-run` | POST | Diff + feasibility + confirm token; **never mutates** | DB (read) |
| 11 | `/api/commits/{id}` | GET | One commit's detail | DB |
| 12 | `/api/commits/{id}/retry` | POST | Re-queue a failed PM write-back push | outbox → PM provider |
| 13 | `/api/audit` | GET | Recent audit rows (already redacted at write time) | DB |
| 14 | `/` | GET | Serves the built dashboard (`frontend/dist`); if unbuilt, returns a JSON hint | filesystem |

**Auth rules:** non-GET routes need `Authorization: Bearer <RIPPLEBACK_API_KEY>` **only when that key is set**. `/api/assistant/chat` and `/api/mcp/tool` are auth-exempt; `/api/mcp/tool` applies the write guard to **write tools only**, so read tools work without a key.

**Which of these the current React UI actually calls:** `/api/health`, `/api/status`, `/api/mcp/tools`, `/api/mcp/tool`, `/api/assistant/chat`. The rest (sync, commits, audit, substitutes) exist for the CLI/other clients and the write path.

---

## 2. MCP server tools (over `:8000/mcp`) — all 17

These are **not** separate HTTP URLs — they are dispatched over the one Streamable-HTTP endpoint `POST/GET http://127.0.0.1:8000/mcp`. The adapter calls them via `RIPPLEBACK_MCP_URL`.

### Read-only tools (12) — safe, no writes

| Tool | What it does |
| --- | --- |
| `get_plan_state` | Full plan for a scenario (entities, dependencies, status, version) |
| `list_scenarios` | Every known scenario (seeded + imported) with light metadata |
| `simulate_disruption` | Hypothetical blast radius of an outage (directly/transitively affected) |
| `analyze_blocked_tasks` | Blocker triage: blocked / at_risk / healthy / done + parallel work + critical path |
| `find_alternative_dependencies` | Recovery options by dropping/bypassing blocking dependencies |
| `find_substitute_options` | Swap a disrupted entity for a substitute candidate |
| `evaluate_recovery_plan` | Structural validity of a proposed change set (not business safety) |
| `rebalance_workload` | Workload reassignment suggestions for staffed plans |
| `recalculate_delivery_forecast` | Relative-duration forecast for a change set / option |
| `validate_hard_constraints` | Hard-constraint + graph validation (cycles, feasibility) |
| `compare_recovery_options` | Rank options by delay/cost/risk/strain/disruption metrics |
| `generate_recovery_schedule` | Relative-duration recovery schedule + baseline comparison |

### Write tools (5) — gated, disabled by default

| Tool | What it does |
| --- | --- |
| `dry_run_recovery_plan` | Compute diff + return an HMAC **confirm token**; no mutation |
| `record_disruption` | Mark a plan disrupted + block an entity (persisted) |
| `resolve_disruption` | Restore prior statuses for an open disruption |
| `commit_recovery_plan` | Apply a confirmed recovery change set (needs token + idempotency key) |
| `commit_reassignment` | Persist a workload reassignment (needs token + idempotency key) |

Write tools require **all** of: `RIPPLEBACK_WRITES_ENABLED=true`, a prior dry-run token bound to `(scenario_id, expected_version, exact changes)`, a unique `idempotency_key`, and a still-matching plan version.

---

## 3. Outbound endpoints (adapter/server → external systems)

| System | Endpoint | Direction | Set by |
| --- | --- | --- | --- |
| MCP server | `http://127.0.0.1:8000/mcp` | adapter → server | `RIPPLEBACK_MCP_URL` |
| Database | `sqlite:///./rippleback.db` (or any SQLAlchemy URL) | server + adapter → DB | `RIPPLEBACK_DB_URL` |
| Jira Cloud | `POST {JIRA_BASE_URL}/rest/api/3/search/jql` (fallback `/rest/api/3/search`); issueLinks/assignee for write-back | sync | `JIRA_*` |
| Linear | `https://api.linear.app/graphql` | sync | `LINEAR_*` |
| GitHub | `https://api.github.com` (issues) | sync | `GITHUB_*` |
| PagerDuty | `https://api.pagerduty.com/incidents` | sync (incidents → risk) | `PAGERDUTY_*` |
| Slack | `{SLACK_WEBHOOK_URL}` | notify | `SLACK_WEBHOOK_URL` |
| Email | SMTP `{SMTP_HOST}:{SMTP_PORT}` | notify | `SMTP_*` |
| Bedrock | AWS Bedrock runtime (optional assistant) | assistant | `RIPPLEBACK_BEDROCK_*` |
| Vite dev | browser `/api/*` → `http://127.0.0.1:8001` | browser → adapter (dev) | `RIPPLEBACK_ADAPTER_PORT` |

---

## 4. Connection matrix (what connects to what)

| From | To | Mechanism | Config |
| --- | --- | --- | --- |
| Browser | Adapter | REST `/api/*` (dev via Vite proxy) | `RIPPLEBACK_ADAPTER_PORT` (default 8001) |
| Adapter | MCP server | MCP Streamable HTTP | `RIPPLEBACK_MCP_URL` (default `http://127.0.0.1:8000/mcp`) |
| Adapter | DB | SQLAlchemy | `RIPPLEBACK_DB_URL` |
| MCP server | DB | SQLAlchemy | `RIPPLEBACK_DB_URL` (**same value**) |
| Sync | Jira/Linear/GitHub/PagerDuty | provider HTTP clients | provider env vars |
| Write-back | PM provider | outbox → provider | `RIPPLEBACK_PM_WRITEBACK_ENABLED` |
| Notify | Slack/SMTP | webhook / SMTP | `SLACK_WEBHOOK_URL`, `SMTP_*` |

---

## 5. UI action → endpoint quick reference

| You do this in the UI | It calls |
| --- | --- |
| Page load / reconnect | `GET /api/health`, `GET /api/status` |
| Switch scenario / load plan | `POST /api/mcp/tool` → `get_plan_state` |
| Open MCP Inspector | `GET /api/mcp/tools` |
| Simulate a disruption | `POST /api/mcp/tool` → `simulate_disruption`, `analyze_blocked_tasks`, `find_alternative_dependencies`, `compare_recovery_options` |
| Dependency graph / impact / timeline / review | `POST /api/mcp/tool` → the relevant read tools |
| Send a chat message | `POST /api/assistant/chat` |

**Startup order:** server (`:8000`) → adapter (`:8001`) → open `http://127.0.0.1:8001` (or Vite `:5173`). The DB auto-creates on first start of whichever backend process runs first.
