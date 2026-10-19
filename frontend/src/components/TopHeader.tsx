/**
 * TopHeader — current page, scenario context, and real connection status.
 *
 * Everything shown here comes from live application state:
 *  - plan version/status from get_plan_state,
 *  - MCP connectivity from /api/health (checked on mount and on demand),
 *  - assistant provider from the adapter's health payload.
 * "Connected" is never displayed merely because the UI loaded.
 */
import { useState } from 'react';
import { clsx } from 'clsx';
import {
  Menu, ChevronDown, RefreshCw, MessageSquare, X,
  Wifi, WifiOff, AlertTriangle,
} from 'lucide-react';
import { useApp } from '../store/appStore';
import { checkConnection } from '../api/mcpClient';
import { scenarioLabel, scenarioDescription } from '../data/scenarios';
import type { DashboardView } from '../types';

const VIEW_TITLES: Record<DashboardView, { title: string; crumb: string }> = {
  overview:    { title: 'Overview',          crumb: 'Control Center' },
  graph:       { title: 'Dependency Graph',  crumb: 'Plan Structure' },
  disruptions: { title: 'Disruptions',       crumb: 'Simulator' },
  impact:      { title: 'Impact Analysis',   crumb: 'Blocker Triage' },
  recovery:    { title: 'Recovery Planner',  crumb: 'Options & Scheduling' },
  timeline:    { title: 'Schedule',          crumb: 'Relative Timeline' },
  review:      { title: 'Review Plan',       crumb: 'Structural Evaluation' },
  activity:    { title: 'Activity Log',      crumb: 'Session Events' },
  mcp:         { title: 'MCP Inspector',     crumb: 'Developer' },
};

interface Props {
  onOpenMobileNav: () => void;
}

export function TopHeader({ onOpenMobileNav }: Props) {
  const [state, dispatch] = useApp();
  const { activeView, activeScenarioId, scenarios, planData, disruption, mcpStatus, assistantOpen, loading } = state;
  const [checking, setChecking] = useState(false);
  const [scenarioMenuOpen, setScenarioMenuOpen] = useState(false);

  const view = VIEW_TITLES[activeView];
  const activeSummary = scenarios.find((s) => s.scenario_id === activeScenarioId);
  const activeLabel = scenarioLabel(activeScenarioId, activeSummary);
  const isLoadingPlan = loading['loadPlan'] ?? false;

  async function handlePing() {
    setChecking(true);
    const status = await checkConnection();
    dispatch({ type: 'SET_MCP_STATUS', status });
    setChecking(false);
  }

  return (
    <header className="flex items-center gap-3 px-3 sm:px-5 py-3 border-b border-surface-700 bg-surface-900/80 backdrop-blur-sm flex-shrink-0">
      {/* Mobile nav toggle */}
      <button
        className="lg:hidden p-1.5 rounded-lg border border-surface-700 text-ink-secondary hover:text-ink"
        onClick={onOpenMobileNav}
        aria-label="Open navigation"
      >
        <Menu className="w-4 h-4" />
      </button>

      {/* Page title */}
      <div className="min-w-0">
        <p className="text-[11px] uppercase tracking-[0.16em] text-ink-muted leading-none mb-1">
          {view.crumb}
        </p>
        <h1 className="text-base sm:text-lg xl:text-xl font-bold text-ink leading-none font-display tracking-tight truncate">
          {view.title}
        </h1>
      </div>

      <div className="flex-1" />

      {/* Simulated badge */}
      {disruption.active && (
        <span className="tag-simulated hidden sm:inline-flex" title="All analysis is hypothetical; the committed plan is not modified">
          <span className="w-1.5 h-1.5 rounded-full bg-warn inline-block" />
          SIMULATED
        </span>
      )}

      {/* Scenario selector */}
      <div className="relative">
        <button
          className="flex items-center gap-2 bg-surface-850 border border-surface-700 rounded-lg pl-3 pr-2 py-1.5 text-left hover:border-surface-600 transition-colors max-w-[210px]"
          onClick={() => setScenarioMenuOpen((v) => !v)}
          aria-haspopup="listbox"
          aria-expanded={scenarioMenuOpen}
          title="Switch scenario"
        >
          <div className="min-w-0">
            <p className="text-xs font-semibold text-ink leading-none truncate">{activeLabel}</p>
            <p className="text-[10px] text-ink-muted leading-none mt-1 font-mono truncate">
              {planData ? `v${planData.version} · ${planData.status}` : isLoadingPlan ? 'loading…' : 'no plan loaded'}
            </p>
          </div>
          <ChevronDown className={clsx('w-3.5 h-3.5 text-ink-muted flex-shrink-0 transition-transform', scenarioMenuOpen && 'rotate-180')} />
        </button>
        {scenarioMenuOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setScenarioMenuOpen(false)} />
            <ul
              role="listbox"
              className="absolute right-0 mt-1 z-50 w-64 rounded-card border border-surface-700 bg-surface-850 shadow-2xl shadow-black/60 overflow-hidden animate-panel-in"
            >
              {scenarios.map((s) => (
                <li key={s.scenario_id}>
                  <button
                    role="option"
                    aria-selected={s.scenario_id === activeScenarioId}
                    className={clsx(
                      'w-full text-left px-3.5 py-2.5 transition-colors',
                      s.scenario_id === activeScenarioId ? 'bg-acid/10' : 'hover:bg-surface-800',
                    )}
                    onClick={() => {
                      dispatch({ type: 'SET_SCENARIO', scenarioId: s.scenario_id });
                      setScenarioMenuOpen(false);
                    }}
                  >
                    <p className={clsx('text-xs font-semibold', s.scenario_id === activeScenarioId ? 'text-acid' : 'text-ink')}>
                      {scenarioLabel(s.scenario_id, s)}
                    </p>
                    <p className="text-[10px] text-ink-muted mt-0.5 leading-relaxed">
                      {scenarioDescription(s) || s.scenario_id}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      {/* MCP connection status (real state, re-checkable) */}
      <button
        onClick={handlePing}
        disabled={checking}
        title={mcpStatus.connected
          ? `${mcpStatus.serverName ?? 'RippleBack'} ${mcpStatus.serverVersion ? `v${mcpStatus.serverVersion}` : ''} · ${mcpStatus.tools?.length ?? 0} tools · click to re-check`
          : `Backend unreachable: ${mcpStatus.error ?? 'no response'} · click to re-check`}
        className={clsx(
          'flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 transition-colors',
          mcpStatus.connected
            ? 'border-ok/40 bg-ok/5 text-ok hover:bg-ok/10'
            : 'border-fail/50 bg-fail/5 text-fail hover:bg-fail/10',
        )}
      >
        {mcpStatus.connected ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
        <span className="text-[11px] font-semibold hidden sm:inline">
          {mcpStatus.connected ? 'Connected' : 'Disconnected'}
        </span>
        {checking && <RefreshCw className="w-3 h-3 animate-spin" />}
        {!mcpStatus.connected && !checking && <AlertTriangle className="w-3 h-3 hidden sm:inline" />}
      </button>

      {/* Assistant provider (real state from the adapter health payload) */}
      {mcpStatus.assistantProvider && (
        <span className="hidden xl:flex items-center gap-1.5 text-[11px] text-ink-muted font-mono" title="Assistant interpretation provider">
          <span className="w-1.5 h-1.5 rounded-full bg-ink-muted" />
          {mcpStatus.assistantProvider}
        </span>
      )}

      {/* Assistant panel toggle */}
      <button
        onClick={() => dispatch({ type: 'SET_ASSISTANT_OPEN', open: !assistantOpen })}
        className={clsx(
          'flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition-colors',
          assistantOpen
            ? 'border-acid/50 bg-acid/10 text-acid'
            : 'border-surface-700 text-ink-secondary hover:text-ink hover:border-surface-600',
        )}
        title={assistantOpen ? 'Hide assistant panel' : 'Show assistant panel'}
        aria-pressed={assistantOpen}
      >
        {assistantOpen ? <X className="w-3.5 h-3.5" /> : <MessageSquare className="w-3.5 h-3.5" />}
        <span className="hidden lg:inline">Assistant</span>
      </button>
    </header>
  );
}
