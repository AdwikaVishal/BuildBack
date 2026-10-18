# RippleBack — Frontend Dashboard

Dark, interactive operations dashboard for the RippleBack disruption intelligence
and recovery planning platform.

**Stack:** React 19 · TypeScript · Vite · Tailwind CSS · React Flow · Recharts

---

## Quick start

The dashboard has three processes. Open three terminal tabs in the
`BuildBack/` root.

### 1 — Python MCP server (backend)

```bash
# Install Python dependencies (once)
pip install -e ".[dev]"

# Start the MCP server on http://127.0.0.1:8000
rippleback-mcp
# or: python -m rippleback.server
```

Environment variables (optional, defaults shown):

```bash
RIPPLEBACK_HOST=127.0.0.1
RIPPLEBACK_PORT=8000
```

### 2 — Node proxy shim

Bridges browser → MCP Streamable HTTP.
Requires the MCP server to be running first.

```bash
cd frontend
node proxy-server.cjs
# Listens on http://127.0.0.1:3001
```

Environment variables forwarded to the shim:

```bash
RIPPLEBACK_HOST=127.0.0.1   # MCP server host
RIPPLEBACK_PORT=8000         # MCP server port
```

### 3 — Vite dev server

```bash
cd frontend
npm run dev
# Opens http://localhost:5173
```

---

## Offline / mock mode

If the MCP server or proxy shim is not running, the dashboard detects the
disconnected state automatically and falls back to **built-in mock data**
derived directly from the `project-full` fixture in `src/rippleback/scenarios.py`.

A `MOCK MODE` badge appears in the Disruption Controls panel.
All panels remain fully interactive using the mock data.

---

## Production build

```bash
cd frontend
npm run build
# Output in frontend/dist/
```

Serve `dist/` behind any static host. In production, replace the Vite proxy
with a reverse proxy (nginx, etc.) forwarding `/api/mcp/*` to the Node shim
(port 3001), and the shim to the Python MCP server (port 8000).

---

## Project structure

```
frontend/
├── proxy-server.cjs          # Node shim: REST → MCP Streamable HTTP
├── vite.config.ts            # Vite config (proxy /api/mcp → :3001)
├── tailwind.config.js        # Dark ops-dashboard colour palette
└── src/
    ├── types/index.ts        # All TypeScript types, aligned to data-contract.md
    ├── data/mockScenario.ts  # Offline mock data (mirrors real backend shapes)
    ├── api/mcpClient.ts      # MCP tool wrappers + call log + connection status
    ├── store/appStore.ts     # Global state (useReducer + Context)
    └── components/
        ├── ui/               # StatusBadge, EvidencePath, Spinner, EmptyState
        ├── Sidebar.tsx
        ├── ScenarioSelector.tsx
        ├── DependencyGraph.tsx   # React Flow graph with status colours + critical path
        ├── DisruptionControls.tsx
        ├── ImpactPanel.tsx
        ├── RecoveryOptions.tsx
        ├── TimelineChart.tsx     # Recharts before/after critical-path comparison
        ├── ApprovalPanel.tsx
        └── MCPInspector.tsx      # Live MCP call log
```

---

## Dashboard panels

| Panel | View key | Description |
|---|---|---|
| Dependency Graph | `graph` | Interactive React Flow graph. Click nodes to inspect state, reason, and evidence path. Critical path highlighted amber. Disrupted entity pulses red. |
| Impact Analysis | `impact` | Triage states (blocked / external / at_risk / healthy / done), evidence paths, parallel work, and the direct/transitive impact chain. |
| Recovery Options | `recovery` | Ranked recovery options from `compare_recovery_options`. Shows score, delay delta, forecast path. Compare mode for side-by-side view. Click "Review & Approve" to proceed. |
| Timeline | `timeline` | Before/after critical-path length bar chart (Recharts). Delay delta highlighted. Path membership chart. All values are relative `duration_units`/`steps`, never wall-clock dates. |
| Approval | `approval` | Structural evaluation (`evaluate_recovery_plan`) and explicit confirm step. Shows verdict, findings, unblocks/still-blocked. Backend is read-only — no write is performed. |
| MCP Inspector | `mcp` | Live tool call log: tool name, duration, arguments, response JSON. Connection status with server name/version and discovered tools. Ping button. |

---

## Workflow

```
Select scenario → (backend loads plan)
     ↓
"Disable Payment API" button
     ↓ (simulate_disruption + analyze_blocked_tasks + find_alternative_dependencies
        + compare_recovery_options called in parallel)
     ↓
Impact Analysis — see which entities are blocked/at-risk
     ↓
Recovery Options — ranked by delay delta; pick one
     ↓
Timeline — before/after critical-path comparison
     ↓
Approval — structural evaluation → confirm (no-op until backend adds writes)
```

---

## Architecture notes

### MCP protocol bridge

Direct browser → MCP (Streamable HTTP / SSE) is impractical without bundling
the full MCP TypeScript SDK. Instead:

```
Browser (fetch JSON)
  → Vite proxy /api/mcp/*
  → Node proxy shim :3001   (proxy-server.cjs)
  → Python MCP server :8000  (JSON-RPC 2.0 / SSE)
```

`proxy-server.cjs` translates `POST /api/mcp/tool { tool, arguments }` into
MCP `tools/call` JSON-RPC and handles both `application/json` and
`text/event-stream` responses from the Python SDK.

### State management

Single `useReducer` + React Context in `src/store/appStore.ts`. No external
state library. The `useAsyncOp(key)` helper tracks per-operation loading/error
state to avoid a separate `useState` in every component.

### Mock data isolation

All mock data is in `src/data/mockScenario.ts`. It is used **only** when the
MCP backend is unreachable. Real backend responses replace mock data when a
connection is established. Mock shapes match the real tool output exactly —
derived directly from `docs/data-contract.md` and `src/rippleback/scenarios.py`.

### No invented capabilities

The frontend never:
- Calculates scores, ranks, or delay deltas itself
- Fabricates committed changes
- Shows a successful commit when the backend has not confirmed one
- Invents endpoints or tool names beyond what `server.py` exposes

---

## Known limitations

- **No write path yet.** `evaluate_recovery_plan` confirms structural validity,
  but the "Confirm Approval" button records intent only. To commit, implement
  a write tool in `src/rippleback/server.py` and update `mcpClient.ts`.
- **`find_substitute_options` always returns `no_substitute_candidates`** for all
  four shipped fixtures — no entity declares `metadata.substitutes`.
- Bundle is ~800 KB (230 KB gzipped) due to ReactFlow + Recharts + Lucide.
  Code-split with dynamic `import()` if bundle size becomes a concern.
- The proxy shim (`proxy-server.cjs`) is single-threaded Node.js, suitable for
  local dev and demos; replace with a proper server for production load.
