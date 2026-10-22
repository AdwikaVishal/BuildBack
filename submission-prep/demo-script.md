# Demo Video Script — under 3:00

> Only behavior verified live during the audit is included. Do NOT narrate
> live Bedrock inference, cloud deployment, PM integrations, offline mode,
> or the write/commit UI flow — none were verified end-to-end in this
> environment. Write refusals ARE verified and are included as the safety beat.
>
> Pre-reqs before recording: the three processes running
> (`python -m rippleback.server`, `python -m rippleback.adapter`, Vite in
> `frontend/`), dashboard at http://localhost:5173, scenario
> `demo-release-plan` selected, browser window ~1440px, no terminal secrets
> visible.

---

## 0:00–0:20 — Hook + architecture

**Show:** dashboard home with the MCP connection indicator green; cut to
`http://127.0.0.1:8001/api/health` (JSON: `"connected": true`,
`"tool_count": 17`).

**Say:** "This is RippleBack. When a service outage hits your release plan,
it tells you what's blocked, what can still ship, and which recovery is
actually feasible — through genuine MCP tools. Seventeen tools, live-discovered
over the Model Context Protocol. The browser never speaks MCP: one backend
adapter owns the official SDK session."

## 0:20–0:45 — Tool discovery

**Show:** `http://127.0.0.1:8001/api/mcp/tools` briefly, then the dashboard's
MCP Inspector view (tool list).

**Say:** "Nothing is hardcoded — the client discovers these tools from the
server at runtime: plan state, blocker triage, recovery alternatives,
constraint validation, comparison, forecasting, scheduling — plus five
write-gated tools that stay dark unless explicitly enabled."

## 0:45–1:50 — Hero workflow (the money shot)

**Show:** Assistant/chat panel. Type (or paste) exactly:

> Our payment API is down. Tell me what is blocked, find recovery
> alternatives, and compare the options.

**While it runs, narrate the visible step trace:**

**Say:** "Watch the trace: it pulls the live plan, runs blocker triage —
three entities blocked with evidence paths — finds recovery alternatives,
then compares them, feeding the real options from step two into the
comparison. No fabricated data: the forecast is relative steps, not made-up
dates. And here's the output: `frontend-integration` can still proceed in
parallel; the recommended structural bypass is
`drop-dependency:backend-integration->payment-api`, labeled as structural
only — human review required."

**Verify on screen before recording further:** the trace shows the five
steps (`get_plan_state`, `analyze_blocked_tasks`,
`find_alternative_dependencies`, `compare_recovery_options`,
`generate_recovery_schedule`) all green.

## 1:50–2:25 — Safety beat

**Show:** in chat, send: "Record a real disruption for payment-api and
commit the recovery plan now." → assistant performs the read-only
*simulation* instead. Then show one direct API refusal (terminal):

```
curl -X POST http://127.0.0.1:8001/api/mcp/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool":"record_disruption","arguments":{"scenario_id":"demo-release-plan","entity_id":"payment-api","idempotency_key":"demo"}}'
```

→ `{"error":"writes are disabled; set RIPPLEBACK_WRITES_ENABLED=true …"}`

**Say:** "The assistant is read-only by construction — it cannot reach the
write tools, and writes are disabled at the server anyway. Nothing was
committed: the plan is still version one, healthy."

## 2:25–2:55 — AI layer, honestly

**Show:** chat metadata showing `provider: deterministic` and the note
`fallback: Bedrock unavailable: RIPPLEBACK_BEDROCK_REGION is not set`.

**Say:** "The assistant has two interpreters: a deterministic one, and an
optional Amazon Bedrock provider using the Converse API — the same
intent-and-slots pattern an Alexa skill uses. We're honest about state:
Bedrock isn't configured in this environment, so it fell back — visibly —
to the deterministic interpreter, and every answer you just saw was still
real MCP tool output. 659 tests pass, including live-protocol integration
tests."

## 2:55–3:00 — Close

**Say:** "RippleBack: disruption recovery your team can argue with — every
step traceable, every claim labeled. Links in the submission."

---

## Recording notes

- Keep API-key headers, `.env`, and any credential material off-screen.
- The plan must still read version 1 / healthy at the end (it will — writes
  are disabled) — worth capturing as the final frame for the checklist.
- If a step misbehaves mid-take, re-run the chat message; it is deterministic
  given the seeded scenario.
