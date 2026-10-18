/**
 * Minimal reactive store using React context + useReducer.
 * No extra dependency — keeps the bundle lean.
 */
import React, { createContext, useContext, useReducer, useCallback, type ReactNode } from 'react';
import type {
  ScenarioId,
  DashboardView,
  Scenario,
  ImpactResult,
  TriageResult,
  RecoveryResult,
  EvaluationResult,
  ComparisonResult,
  MCPConnectionStatus,
  DisruptionSeverity,
  EventType,
} from '../types';

// ── Activity log ──────────────────────────────────────────────────────────────

export type ActivityStatus = 'pending' | 'running' | 'done' | 'failed' | 'awaiting';

export interface ActivityEvent {
  id: string;
  stage: string;
  label: string;
  status: ActivityStatus;
  detail?: string;
  ts: number;
}

export const WORKFLOW_STAGES = [
  'disruption_detected',
  'simulation_completed',
  'impact_analyzed',
  'recovery_generated',
  'option_selected',
  'approval_confirmed',
] as const;

export type WorkflowStage = typeof WORKFLOW_STAGES[number];

export const STAGE_LABELS: Record<WorkflowStage, string> = {
  disruption_detected:  'Disruption detected',
  simulation_completed: 'Simulation completed',
  impact_analyzed:      'Impact analyzed',
  recovery_generated:   'Recovery options generated',
  option_selected:      'Option selected for review',
  approval_confirmed:   'Approval confirmed',
};

// ── State shape ───────────────────────────────────────────────────────────────

export interface AppState {
  // Scenario selection
  activeScenarioId: ScenarioId;
  planData: Scenario | null;
  planLoading: boolean;
  planError: string | null;

  // Active view / panel
  activeView: DashboardView;

  // Disruption simulation state
  disruption: {
    active: boolean;
    entityId: string;
    reason: string;
    severity: DisruptionSeverity;
    eventType: EventType;
    delayMinutes?: number;
    simulated: boolean;
  };

  // Tool result caches
  impactResult: ImpactResult | null;
  triageResult: TriageResult | null;
  recoveryResult: RecoveryResult | null;
  evaluationResult: EvaluationResult | null;
  comparisonResult: ComparisonResult | null;

  // Selected recovery option
  selectedOptionId: string | null;
  approvalPending: boolean;
  approvalConfirmed: boolean;
  commitError: string | null;

  // MCP connection
  mcpStatus: MCPConnectionStatus;

  // Activity / workflow log
  activityLog: ActivityEvent[];

  // Graph: entity highlighted from impact panel click
  highlightedEntityId: string | null;

  // Loading / error per operation
  loading: Record<string, boolean>;
  errors: Record<string, string | null>;
}

const INITIAL_STATE: AppState = {
  activeScenarioId: 'project-full',
  planData: null,
  planLoading: false,
  planError: null,
  activeView: 'graph',
  disruption: {
    active: false,
    entityId: 'payment-api',
    reason: 'Service outage — Payment API is down',
    severity: 'high',
    eventType: 'service_outage',
    simulated: false,
  },
  impactResult: null,
  triageResult: null,
  recoveryResult: null,
  evaluationResult: null,
  comparisonResult: null,
  selectedOptionId: null,
  approvalPending: false,
  approvalConfirmed: false,
  commitError: null,
  mcpStatus: { connected: false, serverUrl: '/api/mcp' },
  activityLog: [],
  highlightedEntityId: null,
  loading: {},
  errors: {},
};

// ── Actions ───────────────────────────────────────────────────────────────────

export type AppAction =
  | { type: 'SET_SCENARIO'; scenarioId: ScenarioId }
  | { type: 'SET_PLAN_LOADING'; loading: boolean }
  | { type: 'SET_PLAN'; plan: Scenario }
  | { type: 'SET_PLAN_ERROR'; error: string }
  | { type: 'SET_VIEW'; view: DashboardView }
  | { type: 'TRIGGER_DISRUPTION'; entityId: string; reason: string; severity: DisruptionSeverity; eventType: EventType; delayMinutes?: number }
  | { type: 'SET_IMPACT'; result: ImpactResult }
  | { type: 'SET_TRIAGE'; result: TriageResult }
  | { type: 'SET_RECOVERY'; result: RecoveryResult }
  | { type: 'SET_EVALUATION'; result: EvaluationResult }
  | { type: 'SET_COMPARISON'; result: ComparisonResult }
  | { type: 'SELECT_OPTION'; optionId: string }
  | { type: 'SET_APPROVAL_PENDING'; pending: boolean }
  | { type: 'CONFIRM_APPROVAL' }
  | { type: 'SET_COMMIT_ERROR'; error: string }
  | { type: 'RESET_DISRUPTION' }
  | { type: 'SET_MCP_STATUS'; status: MCPConnectionStatus }
  | { type: 'SET_LOADING'; key: string; loading: boolean }
  | { type: 'SET_ERROR'; key: string; error: string | null }
  | { type: 'LOG_ACTIVITY'; stage: string; label: string; status: ActivityStatus; detail?: string }
  | { type: 'SET_HIGHLIGHTED_ENTITY'; entityId: string | null };

// ── Reducer ───────────────────────────────────────────────────────────────────

function reducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'SET_SCENARIO':
      return { ...INITIAL_STATE, activeScenarioId: action.scenarioId, mcpStatus: state.mcpStatus };
    case 'SET_PLAN_LOADING':
      return { ...state, planLoading: action.loading, planError: null };
    case 'SET_PLAN':
      return { ...state, planData: action.plan, planLoading: false, planError: null };
    case 'SET_PLAN_ERROR':
      return { ...state, planError: action.error, planLoading: false };
    case 'SET_VIEW':
      return { ...state, activeView: action.view };
    case 'TRIGGER_DISRUPTION':
      return {
        ...state,
        disruption: {
          active: true,
          entityId: action.entityId,
          reason: action.reason,
          severity: action.severity,
          eventType: action.eventType,
          delayMinutes: action.delayMinutes,
          simulated: true,
        },
        // clear stale results
        impactResult: null, triageResult: null, recoveryResult: null,
        evaluationResult: null, comparisonResult: null,
        selectedOptionId: null, approvalPending: false, approvalConfirmed: false, commitError: null,
        highlightedEntityId: null,
      };
    case 'SET_IMPACT':
      return { ...state, impactResult: action.result };
    case 'SET_TRIAGE':
      return { ...state, triageResult: action.result };
    case 'SET_RECOVERY':
      return { ...state, recoveryResult: action.result };
    case 'SET_EVALUATION':
      return { ...state, evaluationResult: action.result };
    case 'SET_COMPARISON':
      return { ...state, comparisonResult: action.result };
    case 'SELECT_OPTION':
      return { ...state, selectedOptionId: action.optionId, approvalPending: false, approvalConfirmed: false, commitError: null };
    case 'SET_APPROVAL_PENDING':
      return { ...state, approvalPending: action.pending };
    case 'CONFIRM_APPROVAL':
      return { ...state, approvalConfirmed: true, approvalPending: false };
    case 'SET_COMMIT_ERROR':
      return { ...state, commitError: action.error, approvalPending: false };
    case 'RESET_DISRUPTION':
      return {
        ...state,
        disruption: { ...INITIAL_STATE.disruption },
        impactResult: null, triageResult: null, recoveryResult: null,
        evaluationResult: null, comparisonResult: null,
        selectedOptionId: null, approvalPending: false, approvalConfirmed: false, commitError: null,
        activityLog: [],
        highlightedEntityId: null,
      };
    case 'SET_MCP_STATUS':
      return { ...state, mcpStatus: action.status };
    case 'SET_LOADING':
      return { ...state, loading: { ...state.loading, [action.key]: action.loading } };
    case 'SET_ERROR':
      return { ...state, errors: { ...state.errors, [action.key]: action.error } };
    case 'LOG_ACTIVITY': {
      const id = `${action.stage}-${Date.now()}`;
      const existing = state.activityLog.findIndex((e) => e.stage === action.stage);
      const event: ActivityEvent = { id, stage: action.stage, label: action.label, status: action.status, detail: action.detail, ts: Date.now() };
      if (existing >= 0) {
        const next = [...state.activityLog];
        next[existing] = event;
        return { ...state, activityLog: next };
      }
      return { ...state, activityLog: [...state.activityLog, event] };
    }
    case 'SET_HIGHLIGHTED_ENTITY':
      return { ...state, highlightedEntityId: action.entityId };
    default:
      return state;
  }
}

// ── Context ───────────────────────────────────────────────────────────────────

const StateCtx = createContext<AppState>(INITIAL_STATE);
const DispatchCtx = createContext<React.Dispatch<AppAction>>(() => undefined);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  return React.createElement(
    StateCtx.Provider,
    { value: state },
    React.createElement(DispatchCtx.Provider, { value: dispatch }, children)
  );
}

export function useAppState(): AppState {
  return useContext(StateCtx);
}

export function useAppDispatch(): React.Dispatch<AppAction> {
  return useContext(DispatchCtx);
}

/** Convenience: both state and dispatch in one hook */
export function useApp(): [AppState, React.Dispatch<AppAction>] {
  return [useAppState(), useAppDispatch()];
}

/** Helper: track async operations with loading/error bookkeeping */
export function useAsyncOp(key: string) {
  const dispatch = useAppDispatch();
  const state = useAppState();

  const run = useCallback(
    async <T>(fn: () => Promise<T>): Promise<T | null> => {
      dispatch({ type: 'SET_LOADING', key, loading: true });
      dispatch({ type: 'SET_ERROR', key, error: null });
      try {
        const result = await fn();
        dispatch({ type: 'SET_LOADING', key, loading: false });
        return result;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        dispatch({ type: 'SET_ERROR', key, error: msg });
        dispatch({ type: 'SET_LOADING', key, loading: false });
        return null;
      }
    },
    [dispatch, key]
  );

  return {
    loading: state.loading[key] ?? false,
    error: state.errors[key] ?? null,
    run,
  };
}
