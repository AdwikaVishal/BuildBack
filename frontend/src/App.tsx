import { useEffect } from 'react';
import { AppProvider, useApp, useAsyncOp } from './store/appStore';
import { Sidebar } from './components/Sidebar';
import { OverviewBar } from './components/OverviewBar';
import { ActivityLog } from './components/ActivityLog';
import { DependencyGraph } from './components/DependencyGraph';
import { DisruptionControls } from './components/DisruptionControls';
import { ImpactPanel } from './components/ImpactPanel';
import { RecoveryOptions } from './components/RecoveryOptions';
import { TimelineChart } from './components/TimelineChart';
import { ApprovalPanel } from './components/ApprovalPanel';
import { MCPInspector } from './components/MCPInspector';
import { getPlanState, checkConnection } from './api/mcpClient';
import { MOCK_SCENARIO } from './data/mockScenario';
import type { ScenarioId } from './types';
import { clsx } from 'clsx';

// ─────────────────────────────────────────────────────────────────────────────

function Dashboard() {
  const [state, dispatch] = useApp();
  const {
    activeView,
    activeScenarioId,
    planData,
    triageResult,
    impactResult,
    recoveryResult,
    comparisonResult,
    selectedOptionId,
    mcpStatus,
    loading,
    errors,
  } = state;

  const { run: runPlan } = useAsyncOp('loadPlan');

  // Load plan on scenario change
  useEffect(() => {
    dispatch({ type: 'SET_PLAN_LOADING', loading: true });
    runPlan(async () => {
      if (mcpStatus.connected === false && mcpStatus.lastCheckedAt) {
        dispatch({ type: 'SET_PLAN', plan: fallbackPlan(activeScenarioId) });
        return;
      }
      try {
        const plan = await getPlanState(activeScenarioId);
        dispatch({ type: 'SET_PLAN', plan });
      } catch {
        dispatch({ type: 'SET_PLAN', plan: fallbackPlan(activeScenarioId) });
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeScenarioId]);

  // Initial connection check
  useEffect(() => {
    checkConnection().then((status) => {
      dispatch({ type: 'SET_MCP_STATUS', status });
      if (status.connected) {
        getPlanState(activeScenarioId)
          .then((plan) => dispatch({ type: 'SET_PLAN', plan }))
          .catch(() => { /* stay on mock */ });
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scenario = planData ?? MOCK_SCENARIO;
  const critPath = triageResult?.critical_path?.path ?? [];

  // Forecast for timeline/approval: prefer selected option, fallback to top recommendation
  const selectedEntry = selectedOptionId
    ? (comparisonResult?.ranked.find((e) => e.option_id === selectedOptionId)
       ?? comparisonResult?.excluded.find((e) => e.option_id === selectedOptionId))
    : null;
  const forecastData = selectedEntry?.forecast
    ?? comparisonResult?.ranked[0]?.forecast
    ?? null;

  // Right panel is shown on graph and impact views
  const showRightPanel = activeView === 'graph' || activeView === 'impact';

  return (
    <div className="flex h-screen overflow-hidden bg-surface-900 text-gray-100">
      <Sidebar />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Overview strip */}
        <OverviewBar />

        {/* Workflow activity log (visible only once disruption starts) */}
        <ActivityLog />

        {/* Main content + optional right panel */}
        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* ── Main content ── */}
          <main className="flex-1 overflow-y-auto min-w-0">
            {activeView === 'graph' && (
              <div className="h-full flex flex-col p-3 gap-3">
                <ViewHeader
                  title="Dependency Graph"
                  subtitle={`${scenario.entities.length} entities · ${scenario.dependencies.length} edges`}
                />
                {loading['loadPlan'] ? (
                  <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">Loading plan…</div>
                ) : (
                  <div className="flex-1 rounded-lg overflow-hidden border border-surface-600 min-h-[400px]">
                    <DependencyGraph
                      scenario={scenario}
                      triageResult={triageResult}
                      criticalPath={critPath}
                    />
                  </div>
                )}
              </div>
            )}

            {activeView === 'impact' && (
              <div className="p-4 space-y-4">
                <ViewHeader
                  title="Impact Analysis"
                  subtitle="Triage states, evidence paths, and parallel work"
                />
                <ImpactPanel
                  triageResult={triageResult}
                  impactResult={impactResult}
                  loading={loading['disruption']}
                  error={errors['disruption']}
                  disconnected={!mcpStatus.connected}
                />
              </div>
            )}

            {activeView === 'recovery' && (
              <div className="p-4 space-y-4">
                <ViewHeader
                  title="Recovery Options"
                  subtitle="Ranked by the backend scoring engine — lower score is better"
                />
                <RecoveryOptions
                  comparisonResult={comparisonResult}
                  recoveryResult={recoveryResult}
                  loading={loading['disruption']}
                  error={errors['disruption']}
                />
              </div>
            )}

            {activeView === 'timeline' && (
              <div className="p-4 space-y-4">
                <ViewHeader
                  title="Before / After Timeline"
                  subtitle="Relative critical-path durations — not wall-clock dates"
                />
                {!selectedOptionId && comparisonResult?.ranked[0] && (
                  <div className="text-xs text-gray-500 bg-surface-800 border border-surface-600 rounded px-3 py-2">
                    Showing forecast for recommended option:{' '}
                    <span className="font-mono text-gray-400">{comparisonResult.recommendation}</span>
                  </div>
                )}
                <TimelineChart forecast={forecastData} loading={loading['disruption']} />
              </div>
            )}

            {activeView === 'approval' && (
              <div className="p-4 space-y-4">
                <ViewHeader
                  title="Review & Approve"
                  subtitle="Structural evaluation before committing a recovery option"
                />
                <ApprovalPanel />
              </div>
            )}

            {activeView === 'mcp' && (
              <div className="p-4 space-y-4">
                <ViewHeader
                  title="MCP Inspector"
                  subtitle="Live tool call log, connection status, discovered tools"
                />
                <MCPInspector />
              </div>
            )}
          </main>

          {/* ── Right panel (graph & impact views) ── */}
          {showRightPanel && (
            <aside className="w-80 flex-shrink-0 overflow-y-auto border-l border-surface-600 bg-surface-900/50">
              <div className="p-4 space-y-4">
                <DisruptionControls />
                {activeView === 'graph' && triageResult && (
                  <TriageSummaryMini summary={triageResult.summary} />
                )}
              </div>
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fallbackPlan(scenarioId: ScenarioId) {
  return MOCK_SCENARIO.scenario_id === scenarioId
    ? MOCK_SCENARIO
    : { ...MOCK_SCENARIO, scenario_id: scenarioId };
}

function ViewHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="flex items-baseline gap-3 flex-shrink-0">
      <h2 className="text-base font-semibold text-white leading-none">{title}</h2>
      {subtitle && <span className="text-xs text-gray-600">{subtitle}</span>}
    </div>
  );
}

function TriageSummaryMini({ summary }: { summary: Record<string, number> }) {
  const rows = [
    { key: 'blocked',  label: 'Blocked',   color: 'text-red-400',     bg: 'bg-red-900/25' },
    { key: 'external', label: 'External',  color: 'text-orange-400',  bg: 'bg-orange-900/25' },
    { key: 'at_risk',  label: 'At Risk',   color: 'text-yellow-400',  bg: 'bg-yellow-900/25' },
    { key: 'healthy',  label: 'Healthy',   color: 'text-emerald-400', bg: 'bg-emerald-900/25' },
    { key: 'done',     label: 'Done',      color: 'text-gray-500',    bg: 'bg-gray-800/30' },
  ];

  return (
    <div className="card p-3 space-y-2">
      <p className="text-[10px] uppercase tracking-wider text-gray-600">Impact Summary</p>
      <div className="grid grid-cols-2 gap-1.5">
        {rows.map(({ key, label, color, bg }) => (
          <div key={key} className={clsx('rounded px-2.5 py-2', bg)}>
            <p className="text-[10px] text-gray-600">{label}</p>
            <p className={clsx('text-xl font-bold font-mono leading-none mt-0.5', color)}>
              {summary[key] ?? 0}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Root ──────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <AppProvider>
      <Dashboard />
    </AppProvider>
  );
}
