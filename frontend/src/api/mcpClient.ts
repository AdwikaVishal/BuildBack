// ─────────────────────────────────────────────────────────────────────────────
// MCP Client — calls the RippleBack MCP server via a Vite-proxied REST shim.
//
// Architecture decision: direct browser→MCP (Streamable HTTP) would require
// vendoring the full MCP TypeScript SDK and handling SSE framing. Instead we
// proxy through Vite's dev-proxy to /api/mcp, which forwards to the Python
// MCP server at http://127.0.0.1:8000. In production, a thin Express/FastAPI
// adapter sits between the browser and the MCP server.
//
// Each tool call emits to the global MCPCallStore so the MCPInspector panel
// can show live traffic without props-drilling.
// ─────────────────────────────────────────────────────────────────────────────

import type {
  Scenario,
  ImpactResult,
  RecoveryResult,
  EvaluationResult,
  WorkloadResult,
  TriageResult,
  ForecastResult,
  ConstraintValidationResult,
  ComparisonResult,
  SubstituteResult,
  ScheduleResult,
  MCPToolCall,
  MCPConnectionStatus,
  MCPToolInfo,
  DisruptionSeverity,
  EventType,
  ChangeItem,
  ScenarioId,
  ScenarioSummary,
  ScoringWeights,
} from '../types';

// Re-export MCPToolCall so MCPInspector can import it from this module.
export type { MCPToolCall };

import { MOCK_MODE, mockToolResult, mockConnectionStatus, mockCallLogEntries, mockToolList } from '../data/mock';

// ── Global call log (observable via subscribe) ────────────────────────────────

type CallListener = (call: MCPToolCall) => void;
const _listeners = new Set<CallListener>();
export const callLog: MCPToolCall[] = MOCK_MODE ? mockCallLogEntries() : [];

export function subscribeCalls(fn: CallListener): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

function _emit(call: MCPToolCall) {
  callLog.push(call);
  if (callLog.length > 200) callLog.shift(); // cap
  _listeners.forEach((fn) => fn(call));
}

// ── Connection status store ───────────────────────────────────────────────────

let _connectionStatus: MCPConnectionStatus = MOCK_MODE
  ? mockConnectionStatus()
  : { connected: false, serverUrl: '/api/mcp' };
type StatusListener = (status: MCPConnectionStatus) => void;
const _statusListeners = new Set<StatusListener>();

export function subscribeStatus(fn: StatusListener): () => void {
  _statusListeners.add(fn);
  return () => _statusListeners.delete(fn);
}
export function getConnectionStatus(): MCPConnectionStatus {
  return _connectionStatus;
}
function _setStatus(patch: Partial<MCPConnectionStatus>) {
  _connectionStatus = { ..._connectionStatus, ...patch };
  _statusListeners.forEach((fn) => fn(_connectionStatus));
}

// ── Core fetch wrapper ────────────────────────────────────────────────────────

/**
 * Parse a response body as JSON and raise a clear, actionable error otherwise.
 * A stale or missing backend route returns plain "Not Found" text, which would
 * otherwise surface as the opaque "Unexpected token 'N' … is not valid JSON".
 */
async function readJson<T>(resp: Response, endpoint: string): Promise<T> {
  const text = await resp.text();
  let parsed: T & { error?: string };
  try {
    parsed = (text ? JSON.parse(text) : {}) as T & { error?: string };
  } catch {
    const snippet = text.trim().slice(0, 120) || '<empty response>';
    throw new Error(
      `${endpoint} returned non-JSON (HTTP ${resp.status}): ${snippet}`,
    );
  }
  if (!resp.ok) {
    throw new Error(parsed?.error ?? `${endpoint} failed (HTTP ${resp.status})`);
  }
  if (parsed?.error) throw new Error(parsed.error);
  return parsed;
}

/**
 * Calls a single MCP tool via the /api/mcp shim.
 *
 * The shim (vite proxy → Python MCP server) accepts:
 *   POST /api/mcp/tool
 *   { tool: string, arguments: Record<string, unknown> }
 * and returns the tool result JSON or { error: string, isError: true }.
 *
 * When the shim is unreachable (backend offline) this throws with a clear
 * message so callers can surface a disconnected state without crashing.
 */
async function callTool<T>(tool: string, args: Record<string, unknown>): Promise<T> {
  const id = `${tool}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const startedAt = Date.now();

  const pending: MCPToolCall = { id, tool, args, startedAt, isError: false };
  _emit(pending);

  // Mock mode: serve the captured fixture instead of reaching the backend.
  if (MOCK_MODE) {
    const fixture = mockToolResult(tool);
    const completedAt = Date.now();
    if (fixture === null) {
      const error = `No mock fixture captured for tool "${tool}"`;
      _emit({ ...pending, completedAt, durationMs: completedAt - startedAt, error, isError: true });
      throw new MCPToolError(tool, error);
    }
    _emit({ ...pending, completedAt, durationMs: completedAt - startedAt, result: fixture, isError: false });
    _setStatus({ connected: true, lastCheckedAt: completedAt });
    return fixture as T;
  }

  let response: Response;
  try {
    response = await fetch('/api/mcp/tool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool, arguments: args }),
      signal: AbortSignal.timeout(30_000),
    });
  } catch (networkErr) {
    const error = networkErr instanceof Error ? networkErr.message : String(networkErr);
    const completedAt = Date.now();
    _emit({ ...pending, completedAt, durationMs: completedAt - startedAt, error, isError: true });
    _setStatus({ connected: false, error, lastCheckedAt: completedAt });
    throw new Error(`MCP server unreachable: ${error}`);
  }

  const completedAt = Date.now();
  const durationMs = completedAt - startedAt;

  let body: { result?: T; error?: string; isError?: boolean };
  try {
    body = await response.json();
  } catch {
    const error = `Non-JSON response (HTTP ${response.status})`;
    _emit({ ...pending, completedAt, durationMs, error, isError: true });
    throw new Error(error);
  }

  if (!response.ok || body.isError || body.error) {
    const error = body.error ?? `HTTP ${response.status}`;
    _emit({ ...pending, completedAt, durationMs, result: body, error, isError: true });
    // 503 from the adapter means the MCP server behind it is unreachable.
    if (response.status === 503) {
      _setStatus({ connected: false, error, lastCheckedAt: completedAt });
    } else {
      _setStatus({ connected: true, lastCheckedAt: completedAt });
    }
    throw new MCPToolError(tool, error);
  }

  _emit({ ...pending, completedAt, durationMs, result: body.result, isError: false });
  _setStatus({ connected: true, lastCheckedAt: completedAt });
  return body.result as T;
}

export class MCPToolError extends Error {
  tool: string;
  constructor(tool: string, message: string) {
    super(message);
    this.tool = tool;
    this.name = 'MCPToolError';
  }
}

// ── Health check ──────────────────────────────────────────────────────────────

export async function checkConnection(): Promise<MCPConnectionStatus> {
  if (MOCK_MODE) {
    const status = mockConnectionStatus();
    _setStatus(status);
    return status;
  }
  try {
    const resp = await fetch('/api/health', { signal: AbortSignal.timeout(5_000) });
    const body = await resp.json() as {
      name?: string; version?: string; tools?: string[]; error?: string;
      mcp?: { connected?: boolean; error?: string };
      assistant?: { provider?: string };
    };
    const assistantProvider = body.assistant?.provider;
    if (!resp.ok || body.error || body.mcp?.connected === false) {
      // The adapter is up but the MCP server behind it is not.
      const status: MCPConnectionStatus = {
        connected: false,
        serverUrl: '/api/mcp',
        error: body.error ?? body.mcp?.error ?? `HTTP ${resp.status}`,
        lastCheckedAt: Date.now(),
        assistantProvider,
      };
      _setStatus(status);
      return status;
    }
    const status: MCPConnectionStatus = {
      connected: true,
      serverUrl: '/api/mcp',
      serverName: body.name,
      serverVersion: body.version,
      tools: body.tools,
      lastCheckedAt: Date.now(),
      assistantProvider,
    };
    _setStatus(status);
    return status;
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    const status: MCPConnectionStatus = {
      connected: false,
      serverUrl: '/api/mcp',
      error,
      lastCheckedAt: Date.now(),
    };
    _setStatus(status);
    return status;
  }
}

// ── Scenario list (live from the adapter) ────────────────────────────────────

/**
 * Fetch the scenarios the backend knows about (seeded fixtures + imported PM
 * plans). The dashboard builds its scenario selector entirely from this list;
 * no scenario ids are hardcoded in the frontend.
 */
export async function fetchScenarioSummaries(): Promise<ScenarioSummary[]> {
  if (MOCK_MODE) {
    return [{ scenario_id: 'demo-release-plan', plan_id: 'Payment API release', source: 'seeded' }];
  }
  const resp = await fetch('/api/status', { signal: AbortSignal.timeout(10_000) });
  const body = await readJson<{ scenarios?: { ids?: string[]; items?: ScenarioSummary[] } }>(
    resp,
    '/api/status',
  );
  if (body.scenarios?.items?.length) return body.scenarios.items;
  // Older adapters only return ids — degrade gracefully.
  return (body.scenarios?.ids ?? []).map((scenario_id) => ({ scenario_id }));
}

// ── Tool discovery (live from the MCP server via the adapter) ────────────────

/**
 * Fetch the live-discovered tool list, including descriptions and JSON input
 * schemas. The list comes from the MCP server's own tools/list — nothing is
 * hardcoded, so newly registered tools (e.g. a scheduling tool) appear here
 * without any frontend change.
 */
export async function listTools(refresh = false): Promise<MCPToolInfo[]> {
  if (MOCK_MODE) {
    return mockToolList();
  }
  const url = refresh ? '/api/mcp/tools?refresh=1' : '/api/mcp/tools';
  const resp = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  const body = await readJson<{ tools?: MCPToolInfo[] }>(resp, '/api/mcp/tools');
  return body.tools ?? [];
}

// ── Tool wrappers ─────────────────────────────────────────────────────────────

export async function getPlanState(scenarioId: ScenarioId): Promise<Scenario> {
  return callTool<Scenario>('get_plan_state', { scenario_id: scenarioId });
}

export async function simulateDisruption(
  scenarioId: ScenarioId,
  entityId: string,
  reason: string,
  severity: DisruptionSeverity = 'high',
): Promise<ImpactResult> {
  return callTool<ImpactResult>('simulate_disruption', {
    scenario_id: scenarioId,
    entity_id: entityId,
    reason,
    severity,
  });
}

export async function findAlternativeDependencies(
  scenarioId: ScenarioId,
  entityId: string,
): Promise<RecoveryResult> {
  return callTool<RecoveryResult>('find_alternative_dependencies', {
    scenario_id: scenarioId,
    entity_id: entityId,
  });
}

export async function evaluateRecoveryPlan(
  scenarioId: ScenarioId,
  optionId: string,
  expectedPlanVersion: number,
): Promise<EvaluationResult> {
  return callTool<EvaluationResult>('evaluate_recovery_plan', {
    scenario_id: scenarioId,
    option_id: optionId,
    expected_plan_version: expectedPlanVersion,
  });
}

export async function rebalanceWorkload(
  scenarioId: ScenarioId,
  expectedPlanVersion: number,
  resourceId?: string,
): Promise<WorkloadResult> {
  return callTool<WorkloadResult>('rebalance_workload', {
    scenario_id: scenarioId,
    expected_plan_version: expectedPlanVersion,
    ...(resourceId ? { resource_id: resourceId } : {}),
  });
}

export async function analyzeBlockedTasks(
  scenarioId: ScenarioId,
  entityId: string,
  eventType: EventType = 'service_outage',
  reason?: string,
  delayMinutes?: number,
): Promise<TriageResult> {
  return callTool<TriageResult>('analyze_blocked_tasks', {
    scenario_id: scenarioId,
    entity_id: entityId,
    event_type: eventType,
    ...(reason ? { reason } : {}),
    ...(delayMinutes != null ? { delay_minutes: delayMinutes } : {}),
  });
}

export async function recalculateDeliveryForecast(
  scenarioId: ScenarioId,
  options: { changes?: ChangeItem[]; optionId?: string; disruptedEntityId?: string },
): Promise<ForecastResult> {
  return callTool<ForecastResult>('recalculate_delivery_forecast', {
    scenario_id: scenarioId,
    ...(options.changes    ? { changes: options.changes }                      : {}),
    ...(options.optionId   ? { option_id: options.optionId }                   : {}),
    ...(options.disruptedEntityId ? { disrupted_entity_id: options.disruptedEntityId } : {}),
  });
}

export async function validateHardConstraints(
  scenarioId: ScenarioId,
  changes?: ChangeItem[],
): Promise<ConstraintValidationResult> {
  return callTool<ConstraintValidationResult>('validate_hard_constraints', {
    scenario_id: scenarioId,
    ...(changes ? { changes } : {}),
  });
}

export async function compareRecoveryOptions(
  scenarioId: ScenarioId,
  options: Array<{ option_id: string; metrics?: Record<string, number | null> }>,
  weights?: Partial<ScoringWeights>,
): Promise<ComparisonResult> {
  return callTool<ComparisonResult>('compare_recovery_options', {
    scenario_id: scenarioId,
    options,
    ...(weights ? { weights } : {}),
  });
}

export async function findSubstituteOptions(
  scenarioId: ScenarioId,
  entityId: string,
): Promise<SubstituteResult> {
  return callTool<SubstituteResult>('find_substitute_options', {
    scenario_id: scenarioId,
    entity_id: entityId,
  });
}

/**
 * generate_recovery_schedule — relative-duration recovery scheduling.
 * Optional change set (the apply_changes shape) and/or a legacy
 * drop-dependency option id, plus an optional disrupted entity whose
 * readiness is recomputed with blocker triage. Never produces calendar
 * dates: the report is in relative units.
 */
export async function generateRecoverySchedule(
  scenarioId: ScenarioId,
  options: { changes?: ChangeItem[]; optionId?: string; disruptedEntityId?: string } = {},
): Promise<ScheduleResult> {
  return callTool<ScheduleResult>('generate_recovery_schedule', {
    scenario_id: scenarioId,
    ...(options.changes    ? { changes: options.changes }              : {}),
    ...(options.optionId   ? { option_id: options.optionId }           : {}),
    ...(options.disruptedEntityId ? { disrupted_entity_id: options.disruptedEntityId } : {}),
  });
}
