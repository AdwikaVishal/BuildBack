# RippleBack Runbook

RippleBack runs as three processes backed by one database:

| Process | Command | Default URL | Role |
| --- | --- | --- | --- |
| MCP server | `python -m rippleback.server` | `http://127.0.0.1:8000/mcp` | Owns every tool and all business logic |
| API adapter | `python -m rippleback.adapter` | `http://127.0.0.1:8001` | The **only** MCP client; serves a REST surface + the built frontend |
| Vite dev UI | `npm run dev` (in `frontend/`) | `http://localhost:5173` | Dashboard (proxies `/api` to the adapter) |

With an empty `.env` this works out of the box on SQLite with the seeded demo
scenarios, writes **disabled**, and no credentials.

## 1. Install (Windows / macOS / Linux)

```powershell
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell
# source .venv/bin/activate       # macOS / Linux
pip install -e ".[dev]"
```

Optional Bedrock assistant: `pip install -e ".[bedrock]"`.

## 2. Start the stack

Either run everything from one command:

```powershell
.venv\Scripts\python.exe -m rippleback up          # MCP server + adapter
```

or use the convenience scripts (they also start the Vite dev server):

```powershell
.\scripts\dev.ps1        # Windows PowerShell
```
```bash
./scripts/dev.sh         # macOS / Linux
```

Or start each terminal by hand:

```powershell
.venv\Scripts\python.exe -m rippleback.server       # terminal 1
.venv\Scripts\python.exe -m rippleback.adapter      # terminal 2
cd frontend; npm install; npm run dev               # terminal 3
```

Sanity check:

```powershell
.venv\Scripts\python.exe -m rippleback doctor       # config + DB report (names only, no secrets)
Invoke-RestMethod http://127.0.0.1:8001/api/health  # { mcp.connected, tools[] }
```

## 3. Data

Three data origins, in precedence order for a given `scenario_id`:

1. **Database** — anything you imported (`seed-sample`, a PM sync, or a `rippleback import-substitutes` run).
2. **Built-in seeded demos** — always available, read-only fixtures.

Load the shipped sample plan/incidents/catalog:

```powershell
.venv\Scripts\python.exe -m rippleback seed-sample samples
```

Then pick a scenario from the dashboard's scenario selector (populated live
from `GET /api/status`). No scenario id is hard-coded in the UI.

## 4. The write path (off by default)

Writes require **all** of:

- `RIPPLEBACK_WRITES_ENABLED=true`
- a dry run first (`POST /api/commits/dry-run`) that returns a `confirm_token`
  bound to the exact `(scenario_id, expected_version, changes)`
- supplying that token plus a unique `idempotency_key` to `POST /api/commits`
- the plan version in the DB still matching `expected_version` (else `409`)

Replaying a commit with the same idempotency key returns the **stored** result
and never applies the change twice. Reusing a key with a different request is
rejected. Set `RIPPLEBACK_API_KEY` before exposing the adapter beyond loopback;
writes are refused outright on a public bind without a key.

Disruptions (`record_disruption` / `resolve_disruption`) follow the same
rules: the plan flips to `disrupted`, the entity is blocked (unless already
`done`), and resolution restores the recorded prior statuses.

The conversational assistant is **read-only** by construction: write tools are
hidden from its model catalog and refused by its tool runner. All mutations go
through the dashboard's dry-run → confirm → commit flow.

## 5. PM sync and write-back

- Set `RIPPLEBACK_PM_PROVIDER` (`jira`, `linear`, or `github`) plus that
  provider's credentials to import real plans — see `docs/INTEGRATIONS.md`.
- `RIPPLEBACK_SYNC_INTERVAL_SECONDS` starts a periodic sync (0 disables it);
  `POST /api/sync` (or `python -m rippleback sync`) runs one pass on demand.
- With `RIPPLEBACK_PM_WRITEBACK_ENABLED=true`, committed recoveries queue in the
  outbox and are pushed idempotently; failures retry and then surface as
  `writeback_failed` (retry from the UI or `POST /api/commits/{id}/retry`).
- PagerDuty (`PAGERDUTY_API_KEY`) feeds incident history for the risk metric.

A sync never erases a local commit the PM has not yet reflected: unreflected
overlay commits are re-applied and only retired once the imported data contains
their effect.

## 6. Offline behavior

Stop the MCP server and the adapter's `/api/health` reports
`mcp.connected: false` (HTTP 503); tool calls return `503` and the assistant
answers honestly (it does not pretend tools ran). The dashboard still renders;
restart the server and the adapter reconnects.

## 7. Troubleshooting

- Ports in use: change `RIPPLEBACK_PORT`, `RIPPLEBACK_ADAPTER_PORT`, or the Vite
  port; check with `Get-NetTCPConnection -LocalPort 8000,8001,5173` (Windows) or
  `lsof -i :8000 -i :8001 -i :5173`.
- "unknown scenario_id" — the assistant and tools list valid ids; re-run
  `seed-sample` or check `GET /api/status`.
- "writes are disabled" — set `RIPPLEBACK_WRITES_ENABLED=true` (and accept that
  changes are real and persisted).
- Missing integration — `rippleback doctor` prints the **exact** variable names
  that are unset; no value is ever printed.
- Frontend not built / serves JSON at `/` — run `npm run build` in `frontend/`
  so the adapter can serve `frontend/dist`.

## 8. Honesty notes (kept intentionally)

- Simulations and forecasts are hypothetical; they never mutate the committed
  plan. Triage/schedule results are marked `simulated: true`.
- Durations are relative `duration_units`, **never calendar dates**.
- `evaluate_recovery_plan` is structural only — it never claims business safety.
- Cost/risk/disruption metrics appear only when a real data source supports
  them; missing values are omitted, never zero-filled.
