/**
 * ImpactPanel — tabbed impact analysis view.
 *
 * Tabs: All · Blocked · External · At Risk · Healthy · Done
 * Clicking an entity dispatches SET_HIGHLIGHTED_ENTITY and switches the graph
 * view so the user can trace the evidence path visually.
 */
import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Play, ArrowRight, ExternalLink, GitBranch } from 'lucide-react';
import type { TriageResult, ImpactResult, TriageState } from '../types';
import { StatusBadge } from './ui/StatusBadge';
import { EvidencePath } from './ui/EvidencePath';
import { EmptyState } from './ui/EmptyState';
import { clsx } from 'clsx';
import { useApp } from '../store/appStore';

interface Props {
  triageResult: TriageResult | null;
  impactResult: ImpactResult | null;
  loading?: boolean;
  error?: string | null;
  disconnected?: boolean;
}

type Tab = 'all' | TriageState;

const TABS: { key: Tab; label: string; color: string }[] = [
  { key: 'all',      label: 'All',      color: 'text-gray-400' },
  { key: 'blocked',  label: 'Blocked',  color: 'text-red-400' },
  { key: 'external', label: 'External', color: 'text-orange-400' },
  { key: 'at_risk',  label: 'At Risk',  color: 'text-yellow-400' },
  { key: 'healthy',  label: 'Healthy',  color: 'text-emerald-400' },
  { key: 'done',     label: 'Done',     color: 'text-gray-500' },
];

export function ImpactPanel({ triageResult, impactResult, loading, error, disconnected }: Props) {
  const [, dispatch] = useApp();
  const [activeTab, setActiveTab] = useState<Tab>('all');

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-20">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <span className="text-sm text-gray-500">Analysing impact…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="m-4 p-4 bg-red-900/20 border border-red-900 rounded-lg">
        <p className="text-sm text-red-400 font-medium mb-1">Analysis failed</p>
        <p className="text-xs text-red-500">{error}</p>
      </div>
    );
  }

  if (!triageResult && !impactResult) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="No disruption active"
        description="Trigger a disruption from the controls panel to see impact analysis."
      />
    );
  }

  const nameMap = triageResult
    ? Object.fromEntries(triageResult.states.map((s) => [s.entity_id, s.name]))
    : {};

  // Tab counts
  const countFor = (tab: Tab) =>
    tab === 'all'
      ? (triageResult?.states.length ?? 0)
      : (triageResult?.summary[tab] ?? 0);

  const filtered = activeTab === 'all'
    ? (triageResult?.states ?? [])
    : (triageResult?.states.filter((s) => s.state === activeTab) ?? []);

  function handleEntityClick(entityId: string) {
    dispatch({ type: 'SET_HIGHLIGHTED_ENTITY', entityId });
    dispatch({ type: 'SET_VIEW', view: 'graph' });
  }

  return (
    <div className="space-y-4">
      {/* Simulated notice */}
      {(triageResult?.simulated ?? impactResult?.simulated) && (
        <div className="flex items-start gap-2 p-3 bg-amber-900/15 border border-amber-900/50 rounded-lg">
          <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-amber-300">
            <strong>Simulated only.</strong> The committed plan is not modified.
            {disconnected && <span className="ml-1 text-amber-500"> Results are mock data (backend offline).</span>}
          </p>
        </div>
      )}

      {/* Summary bar */}
      {triageResult && <SummaryBar summary={triageResult.summary} />}

      {/* Parallel work strip */}
      {triageResult && triageResult.parallel_work.length > 0 && (
        <div className="flex items-center gap-2 p-3 bg-emerald-900/15 border border-emerald-900/40 rounded-lg flex-wrap">
          <div className="flex items-center gap-1.5 text-xs text-emerald-400 font-medium flex-shrink-0">
            <Play className="w-3.5 h-3.5" />
            Can proceed now:
          </div>
          {triageResult.parallel_work.map((work) => (
            <button
              key={work.entity_id}
              onClick={() => handleEntityClick(work.entity_id)}
              className="flex items-center gap-1 bg-emerald-900/30 border border-emerald-800 rounded px-2 py-0.5 text-xs text-emerald-300 hover:bg-emerald-900/50 transition-colors"
              title="Click to highlight on graph"
            >
              <CheckCircle2 className="w-3 h-3" />
              {work.name}
            </button>
          ))}
        </div>
      )}

      {/* Tabs */}
      {triageResult && (
        <div className="card overflow-hidden">
          <div className="flex border-b border-surface-600 overflow-x-auto">
            {TABS.map(({ key, label, color }) => {
              const count = countFor(key);
              if (key !== 'all' && count === 0) return null;
              return (
                <button
                  key={key}
                  onClick={() => setActiveTab(key)}
                  className={clsx(
                    'flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium whitespace-nowrap border-b-2 transition-colors',
                    activeTab === key
                      ? `border-blue-500 ${color} bg-blue-900/10`
                      : 'border-transparent text-gray-500 hover:text-gray-300 hover:bg-surface-700/50',
                  )}
                >
                  <span>{label}</span>
                  <span className={clsx(
                    'text-[10px] font-mono px-1 rounded',
                    activeTab === key ? 'bg-surface-600 text-gray-300' : 'text-gray-600',
                  )}>
                    {count}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Entity rows */}
          <div className="divide-y divide-surface-700">
            {filtered.length === 0 ? (
              <div className="py-8 text-center text-xs text-gray-600">No entities in this category.</div>
            ) : (
              filtered.map((entry) => (
                <EntityRow
                  key={entry.entity_id}
                  entry={entry}
                  nameMap={nameMap}
                  onHighlight={() => handleEntityClick(entry.entity_id)}
                />
              ))
            )}
          </div>
        </div>
      )}

      {/* Impact chain (direct/transitive) from simulate_disruption */}
      {impactResult && (
        <ImpactChain impactResult={impactResult} nameMap={nameMap} onHighlight={handleEntityClick} />
      )}
    </div>
  );
}

// ── Entity row ─────────────────────────────────────────────────────────────────

function EntityRow({
  entry,
  nameMap,
  onHighlight,
}: {
  entry: TriageResult['states'][0];
  nameMap: Record<string, string>;
  onHighlight: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const rowBg: Record<TriageState, string> = {
    blocked:  'hover:bg-red-900/10',
    external: 'hover:bg-orange-900/10',
    at_risk:  'hover:bg-yellow-900/10',
    healthy:  'hover:bg-emerald-900/5',
    done:     'hover:bg-surface-700/30',
  };

  return (
    <div className={clsx('transition-colors', rowBg[entry.state])}>
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <StatusBadge status={entry.state} />
        <span className="text-sm text-gray-200 font-medium flex-1 min-w-0 truncate">{entry.name}</span>

        {/* Highlight on graph button */}
        <button
          title="Show on dependency graph"
          onClick={(e) => { e.stopPropagation(); onHighlight(); }}
          className="flex items-center gap-1 text-[10px] text-gray-600 hover:text-blue-400 transition-colors px-1.5 py-0.5 rounded hover:bg-blue-900/20"
        >
          <GitBranch className="w-3 h-3" />
          <span className="hidden sm:inline">Graph</span>
        </button>

        <span className={clsx('text-[10px] text-gray-700 ml-1', expanded && 'rotate-180')}>▾</span>
      </div>

      {expanded && (
        <div className="px-4 pb-3 space-y-2 border-t border-surface-700/50">
          <p className="text-xs text-gray-500 pt-2 leading-relaxed">{entry.reason}</p>
          {entry.evidence_path.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] text-gray-600 uppercase tracking-wide">Evidence path</p>
              <EvidencePath path={entry.evidence_path} nameMap={nameMap} />
            </div>
          )}
          {entry.evidence_edges.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] text-gray-600 uppercase tracking-wide">Dependency edges</p>
              <div className="flex flex-col gap-1">
                {entry.evidence_edges.map((edge, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-[10px] font-mono text-gray-500">
                    <span className="text-gray-400">{nameMap[edge.prerequisite] ?? edge.prerequisite}</span>
                    <ArrowRight className="w-3 h-3 text-gray-700 flex-shrink-0" />
                    <span className="text-gray-400">{nameMap[edge.dependent] ?? edge.dependent}</span>
                    <span className="ml-auto text-gray-700 italic">{edge.dependency_type}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Summary bar ────────────────────────────────────────────────────────────────

function SummaryBar({ summary }: { summary: TriageResult['summary'] }) {
  const items = [
    { key: 'blocked',  label: 'Blocked',  bg: 'bg-red-500' },
    { key: 'external', label: 'External', bg: 'bg-orange-500' },
    { key: 'at_risk',  label: 'At Risk',  bg: 'bg-yellow-500' },
    { key: 'healthy',  label: 'Healthy',  bg: 'bg-emerald-500' },
    { key: 'done',     label: 'Done',     bg: 'bg-gray-600' },
  ] as const;

  const total = Object.values(summary).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  return (
    <div className="card p-3 space-y-2">
      <div className="flex gap-px h-2.5 rounded overflow-hidden">
        {items.map(({ key, bg }) => {
          const pct = ((summary[key] ?? 0) / total) * 100;
          if (pct === 0) return null;
          return <div key={key} className={clsx('h-full', bg)} style={{ width: `${pct}%` }} title={`${key}: ${summary[key]}`} />;
        })}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {items.map(({ key, label, bg }) => (
          <div key={key} className="flex items-center gap-1.5 text-xs">
            <span className={clsx('w-2 h-2 rounded-full', bg)} />
            <span className="text-gray-500">{label}</span>
            <span className="text-gray-300 font-bold font-mono">{summary[key] ?? 0}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Impact chain ───────────────────────────────────────────────────────────────

function ImpactChain({
  impactResult,
  nameMap,
  onHighlight,
}: {
  impactResult: ImpactResult;
  nameMap: Record<string, string>;
  onHighlight: (id: string) => void;
}) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <ArrowRight className="w-3.5 h-3.5 text-red-400" />
          <span className="text-sm font-medium text-gray-300">Impact Chain</span>
        </div>
        <span className="text-xs text-gray-600 font-mono">
          {impactResult.directly_affected.length}d + {impactResult.transitively_affected.length}t
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Directly affected */}
        <div>
          <p className="text-[10px] uppercase tracking-wide text-gray-600 mb-2">
            Directly affected ({impactResult.directly_affected.length})
          </p>
          {impactResult.directly_affected.length === 0 ? (
            <p className="text-xs text-gray-600">None</p>
          ) : (
            <div className="space-y-1.5">
              {impactResult.directly_affected.map((entry) => (
                <button
                  key={entry.entity_id}
                  onClick={() => onHighlight(entry.entity_id)}
                  className="w-full flex items-center gap-2 text-left hover:bg-surface-700/50 rounded px-2 py-1 transition-colors group"
                >
                  <StatusBadge status={entry.state} />
                  <span className="text-xs text-gray-300">{entry.name}</span>
                  <GitBranch className="w-3 h-3 text-gray-700 group-hover:text-blue-400 ml-auto transition-colors" />
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Transitively affected */}
        <div>
          <p className="text-[10px] uppercase tracking-wide text-gray-600 mb-2">
            Transitively affected ({impactResult.transitively_affected.length})
          </p>
          {impactResult.transitively_affected.length === 0 ? (
            <p className="text-xs text-gray-600">None</p>
          ) : (
            <div className="space-y-2">
              {impactResult.transitively_affected.map((entry) => (
                <div key={entry.entity_id} className="space-y-1">
                  <button
                    onClick={() => onHighlight(entry.entity_id)}
                    className="w-full flex items-center gap-2 text-left hover:bg-surface-700/50 rounded px-2 py-1 transition-colors group"
                  >
                    <StatusBadge status={entry.state} />
                    <span className="text-xs text-gray-300">{entry.name}</span>
                    <GitBranch className="w-3 h-3 text-gray-700 group-hover:text-blue-400 ml-auto transition-colors" />
                  </button>
                  <div className="pl-2">
                    <EvidencePath path={entry.evidence_path} nameMap={nameMap} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Unaffected */}
        {impactResult.unaffected.length > 0 && (
          <div>
            <p className="text-[10px] uppercase tracking-wide text-gray-600 mb-2">
              Unaffected ({impactResult.unaffected.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {impactResult.unaffected.map((entry) => (
                <button
                  key={entry.entity_id}
                  onClick={() => onHighlight(entry.entity_id)}
                  title="Show on graph"
                  className="flex items-center gap-1 text-xs text-gray-500 bg-surface-700 border border-surface-600 rounded px-2 py-0.5 hover:border-blue-700 hover:text-gray-300 transition-colors"
                >
                  <ExternalLink className="w-2.5 h-2.5" />
                  {entry.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
