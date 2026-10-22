# Devpost Submission — Draft

> Team review needed on: final project name, tagline wording, member names,
> repository URL, video URL, and screenshots (see `screenshots-list.md`).
> Nothing below claims unverified functionality; items marked
> *(not yet verified)* or *(future)* must stay marked until true.

---

## Name

**RippleBack**

## Tagline

**When a release plan breaks, ripple effects become a ranked recovery plan —
through genuine MCP tools, with an honest AI assistant that never guesses.**

---

## Problem

Delivery teams discover disruptions late: one service outage silently blocks
an entire release train, and nobody can say *what* is blocked, *what can still
proceed*, or *which recovery option is actually structurally sound* — without
a war room and a whiteboard. Existing PM tools track tickets; they do not
reason about dependency-graph impact, hard constraints, workload, or recovery
feasibility when a critical service goes down.

**Target users:** engineering managers, release/program managers, incident
commanders, and platform teams running dependency-heavy delivery plans
(Jira/Linear/GitHub-managed).

## Solution

RippleBack ingests or seeds a dependency-graph view of a delivery plan and,
after a disruption, deterministically answers the questions a PM actually
asks:

- **Blocker triage** — exactly one state per entity (blocked / external /
  at_risk / healthy / done) with an evidence path, plus the parallel work
  that can still proceed.
- **Recovery alternatives and substitutes** — structural bypass options with
  explicit assumptions and limitations; nothing invented.
- **Feasibility-gated comparison** — options are constraint-validated before
  scoring; infeasible options are excluded, never recommended.
- **Relative delivery forecasts and recovery schedules** — in steps/duration
  units, never fabricated calendar dates.
- **A conversational assistant** — natural language in, real MCP tool
  workflows out, with a visible tool-call trace.

### Key differentiator

**Honesty by construction.** Every hypothetical result is labeled
hypothetical; missing data is reported as missing (e.g.
`insufficient_duration_data`) rather than zero-filled; the chat assistant is
structurally read-only — write tools are refused even if a model asks for
them — and commits require a gated dry-run → confirm → token flow. When the
AI layer is unavailable, a deterministic interpreter takes over *and says so*.

## How the demo works

1. Three local processes: MCP server (business logic), API adapter (the
   genuine MCP client + assistant), and a React dashboard.
2. Demo scenario `demo-release-plan` (6 entities, 4 dependencies) is seeded.
3. The host asks the assistant: *"Our payment API is down. Tell me what is
   blocked, find recovery alternatives, and compare the options."*
4. The assistant runs a live, discovered MCP workflow —
   `get_plan_state → analyze_blocked_tasks → find_alternative_dependencies
   → compare_recovery_options` (+ schedule) — feeding each step's real
   output into the next, and renders evidence paths and a ranked,
   structurally-feasible recommendation.
5. Safety beat: a request to commit is refused by the assistant (read-only),
   and direct write-tool calls are refused because writes are disabled.

See `demo-script.md` for the exact under-three-minute recording script.

## Technical architecture and MCP integration

```
Browser (React dashboard)
   │  REST only — never speaks MCP
   ▼
API adapter (Starlette, :8001)  ← the single genuine MCP client
   │  official Python MCP SDK · Streamable HTTP session
   ▼
MCP server (rippleback.server, :8000/mcp)
   └── 17 registered tools: 12 read-only analysis + 5 write-gated
       (gated behind RIPPLEBACK_WRITES_ENABLED + dry-run confirm token
        + idempotency key)
```

- **Genuine MCP, not HTTP-with-a-label:** the adapter uses the official
  `mcp` Python SDK (`ClientSession` + Streamable HTTP transport) and
  *live-discovers* tools at runtime (`tools/list`) — the dashboard and the
  assistant both operate on the discovered catalog, never a hardcoded list.
  Verified: `/api/health` reports `tool_count: 17` from a live session.
- **Assistant pipeline:** message → intent/parameter extraction (pluggable
  provider) → orchestrator sequences live-discovered MCP tools → structured
  results rendered with evidence. Verified: the payment-API workflow made
  5 real MCP calls (40ms/7ms/4ms/11ms/4ms) and `compare_recovery_options`
  received the exact options returned by the alternatives step.
- **Safety:** the assistant's tool runner refuses the 5 write tools
  (defense-in-depth on top of the env gate); verified refusals returned
  `writes are disabled; set RIPPLEBACK_WRITES_ENABLED=true …`.
- **Tests:** 659/659 passing, including live-transport MCP integration
  tests that exercise a real Streamable HTTP session (not mocks).

## AWS / Bedrock integration status (honest)

**Implemented:** an optional Amazon Bedrock interpretation provider
(`BedrockProvider`) using the `bedrock-runtime` **Converse API** via boto3,
temperature 0.0, strict JSON intent output, tool catalog sent from the live
MCP discovery, and the write-tool catalog deliberately withheld from the
model. Selected with `RIPPLEBACK_ASSISTANT_PROVIDER=bedrock` plus region and
model-ID env vars; credentials come only from the standard AWS backend chain
(never the browser, never committed).

**Verified:** the provider's fallback path — with Bedrock unconfigured, the
assistant still answers using the deterministic interpreter and reports
`fallback: Bedrock unavailable: RIPPLEBACK_BEDROCK_REGION is not set` in the
reply metadata. Unit tests cover this fallback.

**Not yet verified / not yet done:** live Bedrock inference (the audit
environment has no AWS credentials, region, or model access — we do not
claim a single successful model call); no model ID is hardcoded or guessed
by design. **Planned Alexa+ surface:** an Alexa skill fronting the same
MCP workflow via Bedrock intent parsing — *(future; not part of the
current demo)*.

## Challenges, learnings, and future plans

- **Challenge:** browsers cannot speak the MCP protocol; exposing a raw MCP
  session publicly is unsafe. **Learning:** a thin local adapter that owns
  the single MCP session keeps credentials and sessions backend-side and
  gives the frontend a narrow REST surface.
- **Challenge:** LLMs fabricate ids and dates. **Learning:** keep the model
  in the interpretation layer only — deterministic code owns tool selection,
  argument validation against discovered JSON schemas, and explanation;
  the model may only choose intents, never invent tool arguments.
- **Challenge:** AI unavailability must not break the product. **Learning:**
  a tested deterministic fallback with explicit user-visible reasons beats
  a silent failure.
- **Future:** live Bedrock inference + model-driven explanations, an Alexa
  voice surface, authenticated remote MCP hosting, and PM write-back at
  scale.

## Track relevance

- **Primary — Alexa+:** the assistant is exactly the Alexa-shaped
  interaction pattern (natural language → intent + slots → tool invocations
  → spoken-length explanation), with Bedrock as the interpretation layer and
  deterministic safety rails; an Alexa skill is the natural next surface on
  the same MCP tools. *(Skill itself: future.)*
- **AWS Builder mini-challenge:** the Bedrock provider integrates
  `bedrock-runtime` Converse into a real MCP tool loop with production
  concerns handled (timeout, retries, JSON validation, credential-chain-only
  auth, graceful degradation). *(Live inference pending AWS access — see
  honest status above.)*
