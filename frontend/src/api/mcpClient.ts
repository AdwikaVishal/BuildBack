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
  MCPToolCall,
  MCPConnectionStatus,
  DisruptionSeverity,
  EventType,
  ChangeItem,
  ScenarioId,
  ScoringWeights,
} from '../types';

// Re-export MCPToolCall so MCPInspector can import it from this module.
export type { MCPToolCall };

// ── Global call log (observable via subscribe) ────────────────────────────────

type CallListener = (call: MCPToolCall) => void;
const _listeners = new Set<CallListener>();
export const callLog: MCPToolCall[] = [];

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

let _connectionStatus: MCPConnectionStatus = {
  connected: false,
  serverUrl: '/api/mcp',
};
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
    _setStatus({ connected: true, lastCheckedAt: completedAt });
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
  try {
    const resp = await fetch('/api/mcp/health', { signal: AbortSignal.timeout(5_000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json() as { name?: string; version?: string; tools?: string[] };
    const status: MCPConnectionStatus = {
      connected: true,
      serverUrl: '/api/mcp',
      serverName: body.name,
      serverVersion: body.version,
      tools: body.tools,
      lastCheckedAt: Date.now(),
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
