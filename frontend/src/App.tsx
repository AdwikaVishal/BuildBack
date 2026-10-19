/**
 * App shell — black × acid-lime recovery control center.
 *
 * Layout: Sidebar rail · TopHeader · workflow strip · view router ·
 * optional context aside · assistant right panel.
 *
 * Honesty rules enforced here:
 *  - plan data only comes from get_plan_state (no mock fallback), and a
 *    failed load is surfaced as an error state,
 *  - connection status comes from the real health check,
 *  - all analysis views render 'no data' states instead of fabricated results.
 */
import { useEffect, useState } from 'react';
import { AppProvider, useApp, useAsyncOp } from './store/appStore';
import { PanelRightClose, PanelRightOpen, ChevronRight } from 'lucide-react';
import { Sidebar } from './components/Sidebar';
import { TopHeader } from './components/TopHeader';
import { ActivityLog } from './components/ActivityLog';
import { DependencyGraph } from './components/DependencyGraph';
import { ImpactPanel } from './components/ImpactPanel';
import { RecoveryOptions } from './components/RecoveryOptions';
import { TimelineChart } from './components/TimelineChart';
import { ReviewPlan } from './components/ReviewPlan';
import { MCPInspector } from './components/MCPInspector';
import { ChatPanel } from './components/ChatPanel';
import { DisruptionControls } from './components/DisruptionControls';
import { OverviewView } from './components/OverviewView';
import { DisruptionsView } from './components/DisruptionsView';
import { ScheduleView } from './components/ScheduleView';
import { ActivityView } from './components/ActivityView';
import { ErrorState } from './components/ui/ErrorState';
import { Spinner } from './components/ui/Spinner';
import { getPlanState, checkConnection, fetchScenarioSummaries } from './api/mcpClient';
import { clsx } from 'clsx';

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Track viewport width so the shell can make deliberate layout decisions
 * (e.g. never stacking the context aside AND the assistant panel unless the
 * viewport is genuinely wide enough for both).
 */
function useViewportWidth(): number {
  const [width, setWidth] = useState(
    typeof window !== 'undefined' ? window.innerWidth : 1440,
  );
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return width;
}

function Dashboard() {
  const [state, dispatch] = useApp();
  const {
    activeView,
    activeScenarioId,
    planData,
    planError,
    triageResult,
    impactResult,
    recoveryResult,
    comparisonResult,
    selectedOptionId,
    mcpStatus,
    assistantOpen,
    loading,
    errors,
    scenarios,
    scenariosLoading,
    scenariosError,
  } = state;

  const { run: runPlan } = useAsyncOp('loadPlan');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [contextAsideOpen, setContextAsideOpen] = useState(true);
  const [planRetry, setPlanRetry] = useState(0);
  const viewportWidth = useViewportWidth();

  // Initial connection check — real health probe, never assumed.
  useEffect(() => {
    let cancelled = false;
    checkConnection().then((status) => {
      if (cancelled) return;
      dispatch({ type: 'SET_MCP_STATUS', status });
      dispatch({
        type: 'LOG_ACTIVITY',
        stage: 'mcp_connected',
        label: 'MCP client connected',
        status: status.connected ? 'done' : 'failed',
        detail: status.connected
          ? `${status.serverName ?? 'RippleBack'} ${status.serverVersion ? `v${status.serverVersion}` : ''}`
          : status.error,
      });
      if (status.connected) {
        dispatch({
          type: 'LOG_ACTIVITY',
          stage: 'tool_discovery',
          label: 'Tool discovery completed',
          status: 'done',
          detail: `${status.tools?.length ?? 0} tools discovered`,
        });
      }
    });
    return () => { cancelled = true; };
  }, [dispatch]);

  // Assistant panel: open by default only where there is room for it.
  useEffect(() => {
    if (typeof window !== 'undefined' && window.innerWidth >= 1024) {
      dispatch({ type: 'SET_ASSISTANT_OPEN', open: true });
    }
  }, [dispatch]);

  // Load the scenario list from the backend so the selector is never
  // hardcoded. Runs on mount and again whenever the MCP connection recovers.
  useEffect(() => {
    let cancelled = false;
    dispatch({ type: 'SET_SCENARIOS_LOADING', loading: true });
    fetchScenarioSummaries()
      .then((scenarios) => {
        if (cancelled) return;
        dispatch({ type: 'SET_SCENARIOS', scenarios });
      })
      .catch((err) => {
        if (cancelled) return;
        dispatch({
          type: 'SET_SCENARIOS_ERROR',
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => { cancelled = true; };
  }, [mcpStatus.connected, dispatch]);

  // Load plan from the backend (scenario change or reconnect). On failure the
  // plan stays null and the views show an honest error — no mock plan.
  useEffect(() => {
    if (!activeScenarioId) return;
    let cancelled = false;
    runPlan(async () => {
      try {
        const plan = await getPlanState(activeScenarioId);
        if (!cancelled) dispatch({ type: 'SET_PLAN', plan });
      } catch (err) {
        if (!cancelled) {
          dispatch({
            type: 'SET_PLAN_ERROR',
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
    });
    return () => { cancelled = true; };
  }, [activeScenarioId, mcpStatus.connected, planRetry, dispatch, runPlan]);

  const scenario = planData;
  // Critical path for graph highlighting: prefer the live triage result, fall
  // back to the backend comparison baseline (also a real computed path).
  const critPath = triageResult?.critical_path?.path
    ?? comparisonResult?.baseline?.path
    ?? [];

  // Forecast for the timeline section: selected option, else top recommendation
  const selectedEntry = selectedOptionId
    ? (comparisonResult?.ranked.find((e) => e.option_id === selectedOptionId)
       ?? comparisonResult?.excluded.find((e) => e.option_id === selectedOptionId))
    : null;
  const forecastData = selectedEntry?.forecast
    ?? comparisonResult?.ranked[0]?.forecast
    ?? null;

  // Layout decisions — the assistant docks as a column only on lg+; below
  // that it is a full-height overlay (see the aside below).
  const assistantDocked = assistantOpen && viewportWidth >= 1024;

  // The context aside (simulator + triage summary) shares the row with the
  // graph/impact content. It only renders when the viewport can afford it —
  // stacking it next to an open assistant would squeeze the graph below a
  // usable size.
  const wantsContextAside =
    (activeView === 'graph' || activeView === 'impact') && contextAsideOpen;
  const roomForContextAside = assistantDocked
    ? viewportWidth >= 1600
    : viewportWidth >= 1280;
  const showContextAside = wantsContextAside && roomForContextAside;

  const planLoading = loading['loadPlan'] ?? false;

  // Honest "no plan" gate for views that need scenario entities
  const planGate = !scenario && (
    scenariosLoading && scenarios.length === 0 ? (
      <div className="flex flex-col items-center gap-3 py-20">
        <Spinner className="w-7 h-7 text-acid" />
        <span className="text-sm text-ink-muted">Loading scenarios…</span>
      </div>
    ) : scenarios.length === 0 ? (
      <ErrorState
        title="No scenarios available"
        message={scenariosError ?? 'The backend reported no scenarios.'}
        hint={
          <>Load a plan with <span className="font-mono">python -m rippleback seed-sample samples</span> or configure a PM integration, then retry.</>
        }
        onRetry={() => setPlanRetry((k) => k + 1)}
      />
    ) : planLoading ? (
      <div className="flex flex-col items-center gap-3 py-20">
        <Spinner className="w-7 h-7 text-acid" />
        <span className="text-sm text-ink-muted">Loading plan…</span>
      </div>
    ) : (
      <ErrorState
        title="Plan not loaded"
        message={planError ?? 'The backend returned no plan data.'}
        hint={
          mcpStatus.connected
            ? undefined
            : <>Start <span className="font-mono">python -m rippleback.server</span> and <span className="font-mono">python -m rippleback.adapter</span>, then retry.</>
        }
        onRetry={() => setPlanRetry((k) => k + 1)}
      />
    )
  );

  return (
    <div className="flex h-screen overflow-hidden bg-surface-950 text-ink">
      <Sidebar mobileOpen={mobileNavOpen} onCloseMobile={() => setMobileNavOpen(false)} />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <TopHeader onOpenMobileNav={() => setMobileNavOpen(true)} />

        {/* Workflow stage strip (renders nothing until there is activity) */}
        <ActivityLog />

        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* ── Main content ── */}
          <main className="flex-1 overflow-y-auto min-w-0">
            {activeView === 'overview' && (
              <div className="p-4 sm:p-5 lg:p-6">
                <OverviewView />
              </div>
            )}

            {activeView === 'graph' && (
              <div className="h-full flex flex-col p-3 sm:p-4 lg:p-5 gap-3 min-h-[520px]">
                {planGate || (
                  <>
                    <div className="flex items-center gap-3 flex-shrink-0 flex-wrap">
                      <h2 className="text-lg font-semibold text-ink leading-none font-display">Dependency Graph</h2>
                      <span className="text-xs text-ink-muted">
                        {scenario!.entities.length} entities · {scenario!.dependencies.length} edges
                      </span>
                      {/* Context-aside toggle — reclaims graph width when hidden */}
                      <button
                        className={clsx(
                          'ml-auto btn-ghost text-xs px-2.5 py-1.5',
                          showContextAside && 'border-acid/50 text-acid bg-acid/10',
                        )}
                        onClick={() => setContextAsideOpen((v) => !v)}
                        title={showContextAside ? 'Hide simulator panel to enlarge the graph' : 'Show simulator panel'}
                        aria-pressed={showContextAside}
                      >
                        {showContextAside
                          ? <><PanelRightClose className="w-3.5 h-3.5" /> Simulator panel</>
                          : <><PanelRightOpen className="w-3.5 h-3.5" /> Simulator panel</>}
                      </button>
                    </div>
                    <div className="flex-1 rounded-lg overflow-hidden border border-surface-700 min-h-[440px]">
                      <DependencyGraph
                        scenario={scenario!}
                        triageResult={triageResult}
                        criticalPath={critPath}
                      />
                    </div>
                  </>
                )}
              </div>
            )}

            {activeView === 'disruptions' && (
              <div className="p-4 sm:p-5 lg:p-6">
                <DisruptionsView />
              </div>
            )}

            {activeView === 'impact' && (
              <div className="p-4 sm:p-5 lg:p-6 space-y-4">
                <div className="flex items-center gap-3 flex-wrap">
                  <h2 className="text-lg font-semibold text-ink leading-none font-display">Impact Analysis</h2>
                  <span className="text-xs text-ink-muted">Live results from analyze_blocked_tasks &amp; simulate_disruption</span>
                  <button
                    className={clsx(
                      'ml-auto btn-ghost text-xs px-2.5 py-1.5',
                      showContextAside && 'border-acid/50 text-acid bg-acid/10',
                    )}
                    onClick={() => setContextAsideOpen((v) => !v)}
                    title={showContextAside ? 'Hide simulator panel' : 'Show simulator panel'}
                    aria-pressed={showContextAside}
                  >
                    {showContextAside
                      ? <><PanelRightClose className="w-3.5 h-3.5" /> Simulator panel</>
                      : <><PanelRightOpen className="w-3.5 h-3.5" /> Simulator panel</>}
                  </button>
                </div>
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
              <div className="p-4 sm:p-5 lg:p-6 space-y-4">
                <RecoveryOptions
                  comparisonResult={comparisonResult}
                  recoveryResult={recoveryResult}
                  loading={loading['disruption']}
                  error={errors['disruption']}
                />
              </div>
            )}

            {activeView === 'timeline' && (
              <div className="p-4 sm:p-5 lg:p-6 space-y-6">
                <ScheduleView />
                <section className="space-y-3 pt-2 border-t border-surface-700">
                  <div className="flex items-baseline gap-3">
                    <h2 className="text-base font-semibold text-ink font-display">Critical-path forecast</h2>
                    <span className="text-xs text-ink-muted">
                      Relative durations — not wall-clock dates
                    </span>
                  </div>
                  {!selectedOptionId && comparisonResult?.ranked[0] && (
                    <div className="text-xs text-ink-muted bg-surface-850 border border-surface-700 rounded px-3 py-2">
                      Showing forecast for recommended option:{' '}
                      <span className="font-mono text-ink-secondary">{comparisonResult.recommendation}</span>
                    </div>
                  )}
                  <TimelineChart forecast={forecastData} loading={loading['disruption']} />
                </section>
              </div>
            )}

            {activeView === 'review' && (
              <div className="p-4 sm:p-5 lg:p-6 space-y-4 max-w-4xl">
                <ReviewPlan />
              </div>
            )}

            {activeView === 'activity' && (
              <div className="p-4 sm:p-5 lg:p-6">
                <ActivityView />
              </div>
            )}

            {activeView === 'mcp' && (
              <div className="p-4 sm:p-5 lg:p-6">
                <MCPInspector />
              </div>
            )}
          </main>

          {/* ── Context aside (graph & impact) — only when the viewport can afford it ── */}
          {showContextAside && (
            <aside className="hidden xl:flex w-[300px] 2xl:w-[336px] flex-shrink-0 flex-col overflow-y-auto border-l border-surface-700 bg-surface-900/50">
              <div className="p-4 space-y-4">
                <div className="flex items-center justify-between">
                  <p className="label-tech">Simulator</p>
                  <button
                    className="p-1 rounded text-ink-muted hover:text-ink hover:bg-surface-800"
                    onClick={() => setContextAsideOpen(false)}
                    title="Hide simulator panel"
                    aria-label="Hide simulator panel"
                  >
                    <PanelRightClose className="w-4 h-4" />
                  </button>
                </div>
                <DisruptionControls />
                {activeView === 'graph' && triageResult && (
                  <TriageSummaryMini summary={triageResult.summary} />
                )}
              </div>
            </aside>
          )}

          {/* ── Assistant panel — overlay below lg, docked column on lg+ ── */}
          {assistantOpen && (
            <>
              {/* Backdrop below lg (panel is full-height overlay there) */}
              <div
                className="lg:hidden fixed inset-0 z-40 bg-surface-950/70 backdrop-blur-sm"
                onClick={() => dispatch({ type: 'SET_ASSISTANT_OPEN', open: false })}
                aria-hidden="true"
              />
              <aside
                className={clsx(
                  'z-50 flex flex-col flex-shrink-0 border-l border-surface-700 bg-surface-900 animate-panel-in min-w-0',
                  'fixed inset-y-0 right-0 w-full sm:w-[420px]',
                  'lg:static lg:z-auto lg:w-[318px] xl:w-[340px] 2xl:w-[360px]',
                )}
                aria-label="Conversational assistant"
              >
                {/* Desktop collapse grabber on the panel edge */}
                <div className="relative flex-1 min-h-0 flex flex-col">
                  <div className="lg:hidden flex justify-end p-2 border-b border-surface-700">
                    <button
                      className="p-1.5 rounded-lg border border-surface-700 text-ink-secondary hover:text-ink"
                      onClick={() => dispatch({ type: 'SET_ASSISTANT_OPEN', open: false })}
                      aria-label="Close assistant"
                    >
                      ✕
                    </button>
                  </div>
                  <button
                    className="hidden lg:flex absolute left-0 top-1/2 -translate-x-1/2 -translate-y-1/2 z-30 w-5 h-11 rounded-full border border-surface-700 bg-surface-850 items-center justify-center text-ink-muted hover:text-ink hover:border-surface-600 transition-colors"
                    onClick={() => dispatch({ type: 'SET_ASSISTANT_OPEN', open: false })}
                    title="Hide assistant panel (conversation is preserved)"
                    aria-label="Hide assistant panel"
                  >
                    <ChevronRight className="w-3 h-3" />
                  </button>
                  <div className="flex-1 min-h-0">
                    <ChatPanel />
                  </div>
                </div>
              </aside>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function TriageSummaryMini({ summary }: { summary: Record<string, number> }) {
  const rows = [
    { key: 'blocked',  label: 'Blocked',   color: 'text-fail',  bg: 'bg-fail/10' },
    { key: 'external', label: 'External',  color: 'text-info',  bg: 'bg-info/10' },
    { key: 'at_risk',  label: 'At Risk',   color: 'text-warn',  bg: 'bg-warn/10' },
    { key: 'healthy',  label: 'Healthy',   color: 'text-ok',    bg: 'bg-ok/10' },
    { key: 'done',     label: 'Done',      color: 'text-ink-muted', bg: 'bg-surface-800' },
  ];

  return (
    <div className="card p-3 space-y-2">
      <p className="label-tech">Impact Summary</p>
      <div className="grid grid-cols-2 gap-1.5">
        {rows.map(({ key, label, color, bg }) => (
          <div key={key} className={clsx('rounded-lg px-2.5 py-2 border border-surface-700', bg)}>
            <p className="text-[11px] text-ink-muted">{label}</p>
            <p className={clsx('text-2xl font-bold font-mono leading-none mt-0.5', color)}>
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
