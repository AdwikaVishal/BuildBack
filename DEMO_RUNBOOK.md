# RippleBack Demo Runbook — Read-Only Recovery + Scheduling

Scenario: `project-full` (Payment API outage)
Honesty: simulations are hypothetical; plan never modified; relative `duration_units` (no calendar dates).

## 1. Start the application (3 terminals)

```bash
cd /Users/shantanu/BuildBack
.venv/bin/python -m rippleback.server      # Terminal 1: MCP server (http://127.0.0.1:8000/mcp)
.venv/bin/python -m rippleback.adapter     # Terminal 2: MCP adapter (http://127.0.0.1:8001)
cd frontend && npm run dev                 # Terminal 3: Vite UI (http://localhost:5173)
```

Verify healthy stack:
```bash
curl -s http://localhost:5173/api/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('connected', d['mcp']['connected'], 'tools', len(d['tools']))"
```

## 2. Baseline

Open `http://localhost:5173` (if browser available) or inspect via API:
```bash
curl -s -X POST http://localhost:5173/api/mcp/tool -H 'Content-Type: application/json' -d '{"tool":"get_plan_state","arguments":{"scenario_id":"project-full"}}'
```
Expect: `version: 1`, `status: healthy`, `disruptions: []`

## 3. Demo prompt (one prompt triggers the full workflow)

In Chat Panel, enter:
```
The payment API is down. Tell me what is blocked, find recovery alternatives, and compare the options.
```

Expected (actual tool sequence): `get_plan_state → analyze_blocked_tasks → find_alternative_dependencies → compare_recovery_options → generate_recovery_schedule` (5 calls, all ok). Provider: `deterministic`.

Triage (via `analyze_blocked_tasks`, `states`): blocked includes `payment-api`, `backend-integration`, `integration-testing`, `release-validation`, `production-release`; `at_risk` includes `unit-testing`, `release-notes`; healthy includes `vendor-delivery`, `frontend-integration`; `done` includes `auth-service`. `parallel_work`: `vendor-delivery`, `frontend-integration`, `unit-testing`, `release-notes`.

Simulation (`simulate_disruption`, "payment-api", outage): `directly_affected` [`backend-integration`], `transitively_affected` [`integration-testing`, `release-validation`, `production-release`, `unit-testing`], `unaffected` 4. `plan_version` unchanged (1), `simulated: true`.

## 4. Review recovery options & comparison

- Alternatives (`find_alternative_dependencies`): option `drop-dependency:backend-integration->payment-api` bypasses `backend-integration → payment-api` (drop-dependency); `affected_dependency` references both entities. `unblocks`/`limitations` included.
- Comparison (`compare_recovery_options`): outcome `ranked`, includes `baseline`, `ranked`, `excluded`, `feasible_count`, `scorable_count`, `recommendation`. Structurally feasible ≠ business-approved.

## 5. Inspect the proposed schedule

`generate_recovery_schedule` (no changes/option/disruption specified → schedules committed baseline): `outcome: scheduled`, `feasible: true`, `simulated: true`, `unit: duration_units`. Groups show `completed=1` (`auth-service`), `ready_now=9`, `waiting=0`, `blocked=0`. 5 parallel layers, 9-item execution order. `completion.duration: 9.0` (`duration_units`), `covers_all_tasks: true`. Critical path: `payment-api → backend-integration → integration-testing → release-validation → production-release` length 9.0 (hard `requires_completion` edges only; `benefits_from` never define ordering). `missing_data: []`, `violations: []`. Times are relative offsets (`earliest_start/finish`, `waiting_time`), `t=0` = moment of analysis — **no calendar/start date/timezone**.

Also try: “Generate a recovery schedule after this disruption.” (assistant may call `generate_recovery_schedule` with scenario context; verify reply explains hypothetical/read-only nature).

## 6. Verify read-only (must stay unchanged)

```bash
curl -s -X POST http://localhost:5173/api/mcp/tool -H 'Content-Type: application/json' -d '{"tool":"get_plan_state","arguments":{"scenario_id":"project-full"}}' | python3 -c "import json,sys; r=json.load(sys.stdin)['result']; print('v', r['version'], 'st', r['status'], 'disc', r['disruptions'])"
```
Must return `v 1 st healthy disc []`.

## 7. MCP inspector checks (UI)
- Discovered tools: all 11 live (incl. `generate_recovery_schedule`); schemas show `required` as above
- Tool calls appear with status, duration_ms, expandable args/results
- “Hypothetical / read-only” reminder visible in assistant responses
- Provider shown as `deterministic`

## 8. Offline behavior
Stop server (`pkill -f rippleback.server`) or adapter; health becomes `mcp.connected: false`. Tool calls via proxy return 503; assistant replies honestly (no steps executed or transport failure) — UI shows MCP offline; app still serves pages. Restart server + adapter to reconnect.

## 9. Troubleshooting
- Adapter reconnects after server restart (health flips back to connected)
- Port conflicts: check `lsof -i :8000 -i :8001 -i :5173`
- If proxy fails, call adapter directly on 8001
- Unknown scenario → assistant surfaces error listing available IDs

## 10. Important distinctions
- **Simulated vs committed**: disruption/schedule results are `simulated: true`; no `commit_recovery_plan` tool exists — nothing is persisted.
- **Structural feasibility vs business approval**: `evaluate_recovery_plan` is structural only; comparison marks feasibility but never claims business safety.
- **Relative units vs calendar**: `unit: duration_units`, no dates; buffers honored only when `duration_unit == "minutes"`.
- **Gaps** (product-level): commit/approval workflow, PRD PDF absent, frontend visual tests not configured — see `project_progress.json` unresolved_issues.
