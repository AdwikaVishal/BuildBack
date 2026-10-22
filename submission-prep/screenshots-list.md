# Screenshots to Capture (from the live app — none exist yet)

> Capture these yourself from the running stack; do not fabricate, retouch
> results, or reuse fixtures labeled as live. Suggested names below.
> Mark each as captured in the checklist once done.

| # | Suggested filename | Where / how | What it must prove |
|---|---|---|---|
| 1 | `01-dashboard-connected.png` | Dashboard home at `http://localhost:5173` with MCP indicator green/connected | The real frontend runs against the local adapter and shows a live MCP connection (not mock mode — ensure `VITE_MOCK_MODE` is not `true`) |
| 2 | `02-mcp-health-json.png` | Browser at `http://127.0.0.1:8001/api/health` | `"connected": true`, `"tool_count": 17`, `"provider": "deterministic"` — the genuine MCP session and current interpreter |
| 3 | `03-tool-catalog.png` | Dashboard MCP Inspector view (or `/api/mcp/tools` in the browser) | 17 live-discovered tools with descriptions/schemas — discovery, not a hardcoded list |
| 4 | `04-chat-workflow-trace.png` | Assistant panel after the payment-API recovery request, with the full step trace expanded | Five real MCP calls in order (`get_plan_state → analyze_blocked_tasks → find_alternative_dependencies → compare_recovery_options → generate_recovery_schedule`), each with status/duration |
| 5 | `05-triage-evidence.png` | Same reply, triage section | Blocked entities (`payment-api`, `release-validation`, `production-release`) with evidence paths, and `frontend-integration` listed as work that can proceed |
| 6 | `06-ranked-comparison.png` | Same reply, comparison section | The ranked option `drop-dependency:backend-integration->payment-api` with "structural recommendation only" labeling |
| 7 | `07-write-refusal.png` | Terminal showing the `record_disruption` curl and its `writes are disabled…` JSON error | Write gating is real at the API boundary |
| 8 | `08-assistant-read-only.png` | Chat reply to "Record a real disruption … and commit the recovery plan now." | The assistant stays read-only (simulation intent), never touching write tools |
| 9 | `09-bedrock-fallback-note.png` | Chat metadata/notes after a request with `provider: "bedrock"` (or screenshot the JSON reply) | `provider: deterministic` + `fallback: Bedrock unavailable: RIPPLEBACK_BEDROCK_REGION is not set` — honest AI-state reporting |
| 10 | `10-plan-unchanged.png` | `get_plan_state` result (via `/api/mcp/tool`) or dashboard plan view | Version 1, status healthy, 0 open disruptions — proof the demo mutated nothing |

**Optional (only if done honestly before submission):**
- `11-bedrock-live.png` — a chat reply whose metadata shows
  `provider: "bedrock"` and the "interpreted by Bedrock model …" note.
  **Only capture after a real successful inference; do not fabricate.**
- `12-tests.png` — terminal showing `659 passed` from
  `.venv/bin/python -m pytest tests/ -q`.

**Capture settings:** ~1440×900 browser windows, readable font size, no
terminal tabs with credentials, no `.env` visible, no personal Jira/Linear
data (none should be configured locally).
