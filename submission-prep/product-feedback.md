# Product Feedback — Draft answers

> Devpost forms often ask for feedback on the APIs/SDKs used. Answers below
> are grounded in what the audit sessions actually exercised (installation,
  > live MCP client/server sessions, adapter stack, fallback tests).
> Items needing the team's firsthand development experience are marked
> **[TEAM INPUT NEEDED]** — do not invent opinions for those.

---

## Official MCP Python SDK (`mcp`, v2.3.0 — Streamable HTTP)

**What worked well (verified):** the Streamable HTTP transport + official
`ClientSession` gave us a genuine protocol session (initialize, tools/list,
tools/call) against our server with no custom wire code; live tool
discovery means new tools (e.g. the scheduling tool added later) required
zero client changes. `MCPServer` tool registration with JSON-schema
generation from type hints was straightforward, and structured `ToolError`
results surfaced cleanly to the client as `isError` with readable messages.

**Friction observed (verified):** transport failures surface to callers as
`CancelledError` from the SDK's internal anyio task group, which needed
careful handling to distinguish from genuine caller cancellation
(`client.py` documents this via `Task.cancelling()`); SDK v2 renamed some
model attributes (`server_info` vs `serverInfo`), requiring compatibility
shims. Exit-stack teardown must occur in the entering task.

**Suggestions:** document the connection-failure exception contract
explicitly, and keep snake_case/camelCase attribute naming stable across
major versions. **[TEAM INPUT NEEDED]** — any further ergonomics feedback
from day-to-day development.

## Amazon Bedrock (`boto3` bedrock-runtime Converse API)

**What worked well (code-level, verified):** the Converse API's single
`converse()` surface with `inferenceConfig` (temperature 0) is a clean fit
for constrained intent parsing; the standard credential chain means no
credential handling in application code. The provider/fallback architecture
let us ship the integration before credentials existed, which kept the demo
path unblocked.

**Friction observed (verified):** none at runtime — live inference was not
exercised (no AWS credentials/region/model access in the audit
environment), so we have no runtime feedback to give yet.
**[TEAM INPUT NEEDED]** after the first real inference: model latency,
throttle behavior, JSON reliability of the chosen model, console model-access
setup friction.

## Starlette + uvicorn (local adapter)

**Verified:** mounting the single MCP client session behind a small REST
surface with CORS and an optional bearer-key write guard was simple and
stable across the audit (health 503 semantics when the MCP server is down
work as documented). Minor friction: nothing significant to report.
**[TEAM INPUT NEEDED]** for any deeper framework feedback.

## React + Vite dashboard

**Verified (network level):** the dev-server `/api` proxy to the adapter
worked (health JSON through `localhost:5173/api/health`), and the codebase
has an explicit, self-labeling mock mode (`VITE_MOCK_MODE`) that refuses to
fabricate data when offline. Visual/UI feedback is **[TEAM INPUT NEEDED]** —
the audit did not perform a browser-based UI review.

## SQLAlchemy / SQLite persistence

**Verified:** zero-setup local persistence shared by server and adapter;
write-gating, idempotency keys, and confirm-token storage all live behind
it and the full suite (659 tests) passes on it. **[TEAM INPUT NEEDED]** for
Postgres/production persistence experience (not exercised).

## Feedback we'd give the hackathon/program itself

- The MCP + Bedrock combination is a strong pairing for "AI that can act"
  demos; the missing piece for most teams is the *safety* story — ours is
  allowlist + schema validation + read-only surfaces.
- **[TEAM INPUT NEEDED]** — any feedback on hackathon process/mentorship.
