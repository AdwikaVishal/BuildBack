import { RefreshCw, ChevronDown } from 'lucide-react';
import { useApp } from '../store/appStore';
import { SCENARIO_INFO } from '../data/mockScenario';
import type { ScenarioId } from '../types';
import { clsx } from 'clsx';

const SCENARIOS: ScenarioId[] = [
  'project-full',
  'demo-release-plan',
  'demo-release-plan-staffed',
  'travel',
];

export function ScenarioSelector() {
  const [state, dispatch] = useApp();
  const { activeScenarioId, planData, disruption } = state;
  const info = SCENARIO_INFO[activeScenarioId];

  function handleChange(id: ScenarioId) {
    dispatch({ type: 'SET_SCENARIO', scenarioId: id });
  }

  function handleReset() {
    dispatch({ type: 'RESET_DISRUPTION' });
  }

  return (
    <div className="flex items-center gap-3 px-4 py-3 bg-surface-800 border-b border-surface-600">
      {/* Scenario picker */}
      <div className="relative">
        <select
          className="appearance-none bg-surface-700 border border-surface-500 rounded px-3 py-1.5 pr-8 text-sm text-gray-200 cursor-pointer focus:outline-none focus:border-accent-blue"
          value={activeScenarioId}
          onChange={(e) => handleChange(e.target.value as ScenarioId)}
          title="Switch scenario"
        >
          {SCENARIOS.map((id) => (
            <option key={id} value={id}>
              {SCENARIO_INFO[id]?.label ?? id}
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500 pointer-events-none" />
      </div>

      {/* Plan info */}
      <div className="hidden sm:flex items-center gap-2 text-xs text-gray-500">
        <span className="text-gray-400">{info?.description}</span>
        {planData && (
          <>
            <span>·</span>
            <span className="font-mono text-gray-600">v{planData.version}</span>
            <span className="text-gray-700">|</span>
            <span className={clsx(
              'font-mono px-1.5 py-0.5 rounded text-xs',
              planData.status === 'healthy' ? 'text-emerald-400 bg-emerald-900/30' :
              planData.status === 'degraded' ? 'text-yellow-400 bg-yellow-900/30' :
              'text-red-400 bg-red-900/30'
            )}>
              {planData.status}
            </span>
          </>
        )}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Simulated badge */}
      {disruption.active && (
        <span className="tag-simulated text-xs">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" />
          SIMULATED
        </span>
      )}

      {/* Reset */}
      {disruption.active && (
        <button className="btn-ghost text-xs" onClick={handleReset} title="Reset disruption simulation">
          <RefreshCw className="w-3.5 h-3.5" />
          Reset
        </button>
      )}
    </div>
  );
}
