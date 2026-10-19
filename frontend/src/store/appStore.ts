/**
 * Minimal reactive store using React context + useReducer.
 * No extra dependency — keeps the bundle lean.
 */
import React, { createContext, useContext, useReducer, useCallback, type ReactNode } from 'react';
import type {
  ScenarioId,
  ScenarioSummary,
  DashboardView,
  Scenario,
  ImpactResult,
  TriageResult,
  RecoveryResult,
  EvaluationResult,
  ComparisonResult,
  ScheduleResult,
  MCPConnectionStatus,
  DisruptionSeverity,
  EventType,
  ChatMessage,
} from '../types';
import {
  MOCK_MODE,
  mockActivityLog,
  mockChatSeed,
  mockConnectionStatus,
} from '../data/mock';
import {
  MOCK_PLAN,
  MOCK_IMPACT,
  MOCK_TRIAGE,
  MOCK_RECOVERY,
  MOCK_COMPARISON,
  MOCK_EVALUATION,
  MOCK_SCHEDULE,
} from '../data/mockFixtures';

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
  'schedule_generated',
] as const;

export type WorkflowStage = typeof WORKFLOW_STAGES[number];

export const STAGE_LABELS: Record<WorkflowStage, string> = {
  disruption_detected:  'Disruption detected',
  simulation_completed: 'Simulation completed',
  impact_analyzed:      'Impact analyzed',
  recovery_generated:   'Recovery options generated',
  option_selected:      'Option selected for review',
  schedule_generated:   'Proposed schedule generated',
};

// ── State shape ───────────────────────────────────────────────────────────────

export interface AppState {
  // Scenario selection
  activeScenarioId: ScenarioId;
  scenarios: ScenarioSummary[];
  scenariosLoading: boolean;
  scenariosError: string | null;
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
  scheduleResult: ScheduleResult | null;

  // Selected recovery option
  selectedOptionId: string | null;

  // MCP connection
  mcpStatus: MCPConnectionStatus;

  // Activity / workflow log
  activityLog: ActivityEvent[];

  // Graph: entity highlighted from impact panel click
  highlightedEntityId: string | null;

  // Conversational assistant (Person 4) — messages + in-flight flag
  chat: {
    messages: ChatMessage[];
    busy: boolean;
  };

  // Layout chrome
  assistantOpen: boolean;
  sidebarCollapsed: boolean;

  // Loading / error per operation
  loading: Record<string, boolean>;
  errors: Record<string, string | null>;
}

const INITIAL_STATE: AppState = {
  activeScenarioId: '',
  scenarios: MOCK_MODE ? [{ scenario_id: 'demo-release-plan', plan_id: 'Payment API release', source: 'seeded' }] : [],
  scenariosLoading: false,
  scenariosError: null,
  planData: MOCK_MODE ? MOCK_PLAN : null,
  planLoading: false,
  planError: null,
  activeView: 'overview',
  disruption: MOCK_MODE
    ? {
        active: true,
        entityId: MOCK_IMPACT.disruption.entity_id,
        reason: MOCK_IMPACT.disruption.reason,
        severity: MOCK_IMPACT.disruption.severity,
        eventType: 'service_outage',
        delayMinutes: 45,
        simulated: true,
      }
    : {
        active: false,
        entityId: '',
        reason: '',
        severity: 'high',
        eventType: 'service_outage',
        simulated: false,
      },
  impactResult: MOCK_MODE ? MOCK_IMPACT : null,
  triageResult: MOCK_MODE ? MOCK_TRIAGE : null,
  recoveryResult: MOCK_MODE ? MOCK_RECOVERY : null,
  evaluationResult: MOCK_MODE ? MOCK_EVALUATION : null,
  comparisonResult: MOCK_MODE ? MOCK_COMPARISON : null,
  scheduleResult: MOCK_MODE ? MOCK_SCHEDULE : null,
  selectedOptionId: MOCK_MODE ? MOCK_COMPARISON.recommendation ?? null : null,
  mcpStatus: MOCK_MODE ? mockConnectionStatus() : { connected: false, serverUrl: '/api/mcp' },
  activityLog: MOCK_MODE ? mockActivityLog() : [],
  highlightedEntityId: null,
  chat: MOCK_MODE
    ? { messages: mockChatSeed() as ChatMessage[], busy: false }
    : { messages: [], busy: false },
  assistantOpen: false, // opened on desktop mount (see App.tsx)
  sidebarCollapsed: false,
  loading: {},
  errors: {},
};

// ── Actions ───────────────────────────────────────────────────────────────────

export type AppAction =
  | { type: 'SET_SCENARIOS'; scenarios: ScenarioSummary[] }
  | { type: 'SET_SCENARIOS_LOADING'; loading: boolean }
  | { type: 'SET_SCENARIOS_ERROR'; error: string }
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
  | { type: 'SET_SCHEDULE'; result: ScheduleResult | null }
  | { type: 'SELECT_OPTION'; optionId: string }
  | { type: 'RESET_DISRUPTION' }
  | { type: 'SET_MCP_STATUS'; status: MCPConnectionStatus }
  | { type: 'SET_LOADING'; key: string; loading: boolean }
  | { type: 'SET_ERROR'; key: string; error: string | null }
  | { type: 'LOG_ACTIVITY'; stage: string; label: string; status: ActivityStatus; detail?: string }
  | { type: 'CHAT_ADD_MESSAGE'; message: ChatMessage }
  | { type: 'CHAT_UPDATE_MESSAGE'; id: string; patch: Partial<ChatMessage> }
  | { type: 'CHAT_SET_BUSY'; busy: boolean }
  | { type: 'SET_HIGHLIGHTED_ENTITY'; entityId: string | null }
  | { type: 'SET_ASSISTANT_OPEN'; open: boolean }
  | { type: 'SET_SIDEBAR_COLLAPSED'; collapsed: boolean };

// ── Reducer ───────────────────────────────────────────────────────────────────

function reducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'SET_SCENARIOS': {
      const ids = action.scenarios.map((s) => s.scenario_id);
      const keepCurrent = ids.includes(state.activeScenarioId);
      const nextId = keepCurrent ? state.activeScenarioId : (ids[0] ?? '');
      if (keepCurrent) {
        return { ...state, scenarios: action.scenarios, scenariosLoading: false, scenariosError: null };
      }
      // The previously active scenario vanished (e.g. first load, or a PM plan
      // was removed): reset the plan and every derived slice.
      return {
        ...INITIAL_STATE,
        scenarios: action.scenarios,
        activeScenarioId: nextId,
        mcpStatus: state.mcpStatus,
        chat: state.chat,
        assistantOpen: state.assistantOpen,
        sidebarCollapsed: state.sidebarCollapsed,
        activeView: state.activeView,
      };
    }
    case 'SET_SCENARIOS_LOADING':
      return { ...state, scenariosLoading: action.loading };
    case 'SET_SCENARIOS_ERROR':
      return { ...state, scenariosError: action.error, scenariosLoading: false };
    case 'SET_SCENARIO':
      // Conversation history, MCP status, and layout chrome survive scenario
      // switches; the scenario id is sent with each chat message, not baked
      // into history.
      return {
        ...INITIAL_STATE,
        activeScenarioId: action.scenarioId,
        scenarios: state.scenarios,
        mcpStatus: state.mcpStatus,
        chat: state.chat,
        assistantOpen: state.assistantOpen,
        sidebarCollapsed: state.sidebarCollapsed,
        activeView: state.activeView,
      };
    case 'SET_PLAN_LOADING':
      return { ...state, planLoading: action.loading, planError: null };
    case 'SET_PLAN':
      return { ...state, planData: action.plan, planLoading: false, planError: null };
    case 'SET_PLAN_ERROR':
      return { ...state, planError: action.error, planLoading: false };
    case 'SET_VIEW':
      return { ...state, activeView: action.view };
    case 'TRIGGER_DISRUPTION':
      if (MOCK_MODE) {
        // Mock mode: keep the captured result slices on every page — only the
        // disruption description itself changes with the user's input.
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
        };
      }
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
        evaluationResult: null, comparisonResult: null, scheduleResult: null,
        selectedOptionId: null,
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
    case 'SET_SCHEDULE':
      return { ...state, scheduleResult: action.result };
    case 'SELECT_OPTION':
      // A new selection invalidates the previous option's evaluation —
      // never display one option's verdict under another option's name.
      return {
        ...state,
        selectedOptionId: action.optionId,
        evaluationResult: action.optionId === state.selectedOptionId
          ? state.evaluationResult
          : null,
      };
    case 'RESET_DISRUPTION':
      if (MOCK_MODE) {
        // Mock mode: restore the captured post-simulation seed instead of
        // emptying every page.
        return {
          ...state,
          disruption: { ...INITIAL_STATE.disruption },
          impactResult: INITIAL_STATE.impactResult,
          triageResult: INITIAL_STATE.triageResult,
          recoveryResult: INITIAL_STATE.recoveryResult,
          evaluationResult: INITIAL_STATE.evaluationResult,
          comparisonResult: INITIAL_STATE.comparisonResult,
          scheduleResult: INITIAL_STATE.scheduleResult,
          selectedOptionId: INITIAL_STATE.selectedOptionId,
          activityLog: INITIAL_STATE.activityLog,
          highlightedEntityId: null,
        };
      }
      return {
        ...state,
        disruption: { ...INITIAL_STATE.disruption },
        impactResult: null, triageResult: null, recoveryResult: null,
        evaluationResult: null, comparisonResult: null, scheduleResult: null,
        selectedOptionId: null,
        activityLog: [],
        highlightedEntityId: null,
      };
    case 'SET_MCP_STATUS':
      // Mock mode: the connection is always the captured connected status.
      return { ...state, mcpStatus: MOCK_MODE ? mockConnectionStatus() : action.status };
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
    case 'SET_ASSISTANT_OPEN':
      return { ...state, assistantOpen: action.open };
    case 'SET_SIDEBAR_COLLAPSED':
      return { ...state, sidebarCollapsed: action.collapsed };
    case 'CHAT_ADD_MESSAGE':
      return { ...state, chat: { ...state.chat, messages: [...state.chat.messages, action.message] } };
    case 'CHAT_UPDATE_MESSAGE':
      return {
        ...state,
        chat: {
          ...state.chat,
          messages: state.chat.messages.map((m) =>
            m.id === action.id ? { ...m, ...action.patch } : m,
          ),
        },
      };
    case 'CHAT_SET_BUSY':
      return { ...state, chat: { ...state.chat, busy: action.busy } };
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
