/**
 * OverviewBar — compact status strip displayed at the top of the dashboard.
 * Shows scenario info, disruption status, entity counts, forecast delta,
 * recovery option count, and MCP connection status.
 * All metrics are derived from real backend results (or clearly-labelled mock).
 */
import { clsx } from 'clsx';
import { Wifi, WifiOff, AlertTriangle, RefreshCw, Zap } from 'lucide-react';
import { useApp } from '../store/appStore';
import { SCENARIO_INFO } from '../data/mockScenario';

export function OverviewBar() {
  const [state, dispatch] = useApp();
  const {
    activeScenarioId,
    planData,
    disruption,
    triageResult,
    comparisonResult,
    mcpStatus,
    loading,
  } = state;

  const info = SCENARIO_INFO[activeScenarioId];
  const summary = triageResult?.summary;
  const feasibleCount = comparisonResult?.feasible_count ?? null;
  const recommendation = comparisonResult?.recommendation;

  // Forecast delta from recommendation
  const recEntry = recommendation
    ? comparisonResult?.ranked.find((r) => r.option_id === recommendation)
    : null;
  const delta = recEntry?.forecast?.delay_delta ?? null;
  const unit  = recEntry?.forecast?.unit ?? '';

  const isLoadingPlan = loading['loadPlan'];

  return (
    <div className="flex items-center gap-0 border-b border-surface-600 bg-surface-800/80 backdrop-blur-sm flex-shrink-0 overflow-x-auto">
      {/* Scenario identity */}
      <div className="flex items-center gap-2.5 px-4 py-2.5 border-r border-surface-600 flex-shrink-0">
        <div className="w-6 h-6 rounded bg-gradient-to-br from-blue-600 to-purple-600 flex items-center justify-center flex-shrink-0">
          <Zap className="w-3.5 h-3.5 text-white" />
        </div>
        <div className="min-w-0">
          <p className="text-xs font-semibold text-white leading-tight truncate">{info?.label ?? activeScenarioId}</p>
          <p className="text-[10px] text-gray-500 leading-tight font-mono">
            {planData ? `v${planData.version} · ${planData.status}` : (isLoadingPlan ? 'loading…' : 'no plan')}
          </p>
        </div>
      </div>

      {/* Disruption status */}
      <MetricCell
        label="Disruption"
        className="border-r border-surface-600"
      >
        {disruption.active ? (
          <span className="flex items-center gap-1 text-red-400 font-medium">
            <AlertTriangle className="w-3 h-3" />
            <span className="text-xs">Active</span>
            <span className="text-[10px] text-red-600 font-mono truncate max-w-[120px]" title={disruption.entityId}>
              {disruption.entityId}
            </span>
          </span>
        ) : (
          <span className="text-gray-600 text-xs">None</span>
        )}
      </MetricCell>

      {/* Entity state counts */}
      {summary ? (
        <div className="flex items-center border-r border-surface-600 flex-shrink-0">
          {[
            { key: 'blocked',  color: 'text-red-400',     dot: 'bg-red-500'     },
            { key: 'external', color: 'text-orange-400',  dot: 'bg-orange-500'  },
            { key: 'at_risk',  color: 'text-yellow-400',  dot: 'bg-yellow-500'  },
            { key: 'healthy',  color: 'text-emerald-400', dot: 'bg-emerald-500' },
            { key: 'done',     color: 'text-gray-500',    dot: 'bg-gray-600'    },
          ].map(({ key, color, dot }) => (
            <div key={key} className="flex items-center gap-1 px-3 py-2.5" title={key}>
              <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', dot)} />
              <span className={clsx('text-sm font-bold font-mono leading-none', color)}>
                {summary[key] ?? 0}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <MetricCell label="States" className="border-r border-surface-600">
          <span className="text-gray-600 text-xs">{disruption.active ? '—' : 'run simulation'}</span>
        </MetricCell>
      )}

      {/* Forecast delta */}
      <MetricCell label="Δ Delay" className="border-r border-surface-600">
        {delta != null ? (
          <span className={clsx(
            'text-sm font-bold font-mono',
            delta > 0 ? 'text-yellow-400' : delta < 0 ? 'text-emerald-400' : 'text-gray-400',
          )}>
            {delta > 0 ? '+' : ''}{delta}
            <span className="text-[10px] font-normal text-gray-600 ml-0.5">{unit}</span>
          </span>
        ) : (
          <span className="text-gray-600 text-xs">—</span>
        )}
      </MetricCell>

      {/* Recovery options */}
      <MetricCell label="Options" className="border-r border-surface-600">
        {feasibleCount != null ? (
          <span className="text-sm font-bold font-mono text-blue-400">{feasibleCount}</span>
        ) : (
          <span className="text-gray-600 text-xs">—</span>
        )}
      </MetricCell>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Simulated badge */}
      {disruption.active && (
        <div className="px-3 py-2.5 flex-shrink-0">
          <span className="tag-simulated text-[10px]">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block mr-1" />
            SIMULATED
          </span>
        </div>
      )}

      {/* Reset */}
      {disruption.active && (
        <div className="px-2 py-2.5 flex-shrink-0 border-l border-surface-600">
          <button
            className="btn-ghost text-[10px] py-1"
            onClick={() => dispatch({ type: 'RESET_DISRUPTION' })}
            title="Reset to baseline"
          >
            <RefreshCw className="w-3 h-3" />
            Reset
          </button>
        </div>
      )}

      {/* MCP status */}
      <div className={clsx(
        'flex items-center gap-1.5 px-3 py-2.5 border-l border-surface-600 flex-shrink-0 text-xs',
        mcpStatus.connected ? 'text-emerald-400' : 'text-red-500',
      )}>
        {mcpStatus.connected
          ? <Wifi className="w-3.5 h-3.5" />
          : <WifiOff className="w-3.5 h-3.5" />
        }
        <span className="hidden md:inline text-[10px]">
          {mcpStatus.connected ? 'MCP' : 'mock'}
        </span>
      </div>
    </div>
  );
}

function MetricCell({ label, children, className }: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('flex flex-col justify-center px-3 py-2 flex-shrink-0 min-w-[80px]', className)}>
      <span className="text-[9px] uppercase tracking-wider text-gray-600 leading-none mb-0.5">{label}</span>
      <div className="leading-none">{children}</div>
    </div>
  );
}
