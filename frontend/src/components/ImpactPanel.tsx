/**
 * ImpactPanel — tabbed impact analysis view.
 *
 * Tabs: All · Blocked · External · At Risk · Healthy · Done
 * Clicking an entity dispatches SET_HIGHLIGHTED_ENTITY and switches the graph
 * view so the user can trace the evidence path visually.
 *
 * All data shown comes from live MCP tool results — there are no mock
 * fallbacks. Failures surface as explicit errors.
 */
import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Play, ArrowRight, ExternalLink, GitBranch } from 'lucide-react';
import type { TriageResult, ImpactResult, TriageState } from '../types';
import { StatusBadge } from './ui/StatusBadge';
import { EvidencePath } from './ui/EvidencePath';
import { EmptyState } from './ui/EmptyState';
import { ErrorState } from './ui/ErrorState';
import { Spinner } from './ui/Spinner';
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
  { key: 'all',      label: 'All',      color: 'text-ink-secondary' },
  { key: 'blocked',  label: 'Blocked',  color: 'text-fail' },
  { key: 'external', label: 'External', color: 'text-info' },
  { key: 'at_risk',  label: 'At Risk',  color: 'text-warn' },
  { key: 'healthy',  label: 'Healthy',  color: 'text-ok' },
  { key: 'done',     label: 'Done',     color: 'text-ink-muted' },
];

export function ImpactPanel({ triageResult, impactResult, loading, error, disconnected }: Props) {
  const [state, dispatch] = useApp();
  const [activeTab, setActiveTab] = useState<Tab>('all');

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-20">
        <Spinner className="w-7 h-7 text-acid" />
        <span className="text-sm text-ink-muted">Analysing impact…</span>
      </div>
    );
  }

  if (error) {
    return <ErrorState title="Analysis failed" message={error} />;
  }

  if (!triageResult && !impactResult) {
    // Distinguish "nothing was simulated" from "a disruption exists but its
    // results could not be loaded" — never misstate one as the other.
    return disconnected ? (
      <EmptyState
        icon={AlertTriangle}
        title="Impact results unavailable"
        description={
          state.disruption.active
            ? `The backend is disconnected, so the impact of the active disruption (${state.disruption.entityId}) cannot be loaded. No results are shown until it reconnects.`
            : 'The backend is disconnected. Run a simulation once it reconnects — no results are shown in the meantime.'
        }
      />
    ) : (
      <EmptyState
        icon={AlertTriangle}
        title="No disruption active"
        description="Trigger a disruption from the controls panel to see impact analysis."
      />
    );
  }

  // Entity names for evidence paths: from triage states, falling back to the
  // impact result's own names so paths never render as raw IDs.
  const nameMap: Record<string, string> = {
    ...(triageResult
      ? Object.fromEntries(triageResult.states.map((s) => [s.entity_id, s.name]))
      : {}),
    ...(impactResult
      ? Object.fromEntries([
          ...impactResult.directly_affected.map((e) => [e.entity_id, e.name]),
          ...impactResult.transitively_affected.map((e) => [e.entity_id, e.name]),
          ...impactResult.unaffected.map((e) => [e.entity_id, e.name]),
        ])
      : {}),
  };

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
        <div className="flex items-start gap-2 p-3 bg-warn/10 border border-warn/30 rounded-lg">
          <AlertTriangle className="w-4 h-4 text-warn flex-shrink-0 mt-0.5" />
          <p className="text-xs text-warn">
            <strong>Simulated only.</strong> The committed plan is not modified.
          </p>
        </div>
      )}

      {/* Backend disruption details + critical path — real analyze_blocked_tasks output */}
      {triageResult && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="card p-4 space-y-2">
            <p className="label-tech">Disruption (from backend)</p>
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge status="blocked" />
              <span className="text-sm text-ink font-medium">{triageResult.disruption.name}</span>
              <span className="badge bg-surface-800 border border-surface-700 text-ink-muted">
                {triageResult.disruption.event_type}
              </span>
            </div>
            {triageResult.disruption.reason && (
              <p className="text-xs text-ink-secondary leading-relaxed">{triageResult.disruption.reason}</p>
            )}
            <p className="text-[11px] text-ink-muted">
              Simulated delay:{' '}
              <span className="font-mono text-warn">
                {triageResult.disruption.delay_minutes != null
                  ? `${triageResult.disruption.delay_minutes} minutes`
                  : 'Not available'}
              </span>
            </p>
          </div>

          <div className="card p-4 space-y-2">
            <p className="label-tech">Critical path</p>
            <p className="text-xs text-ink-secondary">
              Length:{' '}
              <span className="font-mono text-ink font-bold">
                {triageResult.critical_path.length} {triageResult.critical_path.unit}
              </span>{' '}
              (relative units)
            </p>
            <div className="flex flex-wrap gap-1">
              {triageResult.critical_path.path.map((id, i) => (
                <span key={`${id}-${i}`} className="badge bg-acid/5 border border-acid/30 text-ink-secondary font-mono">
                  {nameMap[id] ?? id}
                </span>
              ))}
            </div>
            {triageResult.critical_path_change?.impacted_entity_ids?.length > 0 && (
              <p className="text-[11px] text-warn">
                Entities on the disrupted path:{' '}
                <span className="font-mono">
                  {triageResult.critical_path_change.impacted_entity_ids
                    .map((id) => nameMap[id] ?? id)
                    .join(' → ')}
                </span>
              </p>
            )}
            {(triageResult.critical_path_change?.assumptions ?? []).slice(0, 1).map((a, i) => (
              <p key={i} className="text-[11px] text-ink-muted italic">{a}</p>
            ))}
          </div>
        </div>
      )}

      {/* Summary bar */}
      {triageResult && <SummaryBar summary={triageResult.summary} />}

      {/* Parallel work strip */}
      {triageResult && triageResult.parallel_work.length > 0 && (
        <div className="flex items-center gap-2 p-3 bg-ok/10 border border-ok/30 rounded-lg flex-wrap">
          <div className="flex items-center gap-1.5 text-xs text-ok font-medium flex-shrink-0">
            <Play className="w-3.5 h-3.5" />
            Can proceed now:
          </div>
          {triageResult.parallel_work.map((work) => (
            <button
              key={work.entity_id}
              onClick={() => handleEntityClick(work.entity_id)}
              className="flex items-center gap-1 bg-ok/15 border border-ok/40 rounded px-2 py-0.5 text-xs text-ok hover:bg-ok/25 transition-colors"
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
          <div className="flex border-b border-border overflow-x-auto">
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
                      ? `border-acid ${color} bg-acid/5`
                      : 'border-transparent text-ink-muted hover:text-ink hover:bg-surface-700/50',
                  )}
                >
                  <span>{label}</span>
                  <span className={clsx(
                    'text-[11px] font-mono px-1 rounded',
                    activeTab === key ? 'bg-surface-700 text-ink-secondary' : 'text-ink-muted',
                  )}>
                    {count}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Entity rows */}
          <div className="divide-y divide-border">
            {filtered.length === 0 ? (
              <div className="py-8 text-center text-xs text-ink-muted">No entities in this category.</div>
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
    blocked:  'hover:bg-fail/5',
    external: 'hover:bg-info/5',
    at_risk:  'hover:bg-warn/5',
    healthy:  'hover:bg-ok/5',
    done:     'hover:bg-surface-700/30',
  };

  return (
    <div className={clsx('transition-colors', rowBg[entry.state])}>
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <StatusBadge status={entry.state} />
        <span className="text-sm text-ink font-medium flex-1 min-w-0 truncate">{entry.name}</span>

        {/* Highlight on graph button */}
        <button
          title="Show on dependency graph"
          onClick={(e) => { e.stopPropagation(); onHighlight(); }}
          className="flex items-center gap-1 text-[11px] text-ink-muted hover:text-acid transition-colors px-1.5 py-0.5 rounded hover:bg-acid/10"
        >
          <GitBranch className="w-3 h-3" />
          <span className="hidden sm:inline">Graph</span>
        </button>

        <span className={clsx('text-[11px] text-ink-muted ml-1', expanded && 'rotate-180')}>▾</span>
      </div>

      {expanded && (
        <div className="px-4 pb-3 space-y-2 border-t border-border/60">
          <p className="text-xs text-ink-secondary pt-2 leading-relaxed">{entry.reason}</p>
          {entry.evidence_path.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] text-ink-muted uppercase tracking-wide">Evidence path</p>
              <EvidencePath path={entry.evidence_path} nameMap={nameMap} />
            </div>
          )}
          {entry.evidence_edges.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] text-ink-muted uppercase tracking-wide">Dependency edges</p>
              <div className="flex flex-col gap-1">
                {entry.evidence_edges.map((edge, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-[11px] font-mono text-ink-muted">
                    <span className="text-ink-secondary">{nameMap[edge.prerequisite] ?? edge.prerequisite}</span>
                    <ArrowRight className="w-3 h-3 text-border flex-shrink-0" />
                    <span className="text-ink-secondary">{nameMap[edge.dependent] ?? edge.dependent}</span>
                    <span className="ml-auto text-ink-muted italic">{edge.dependency_type}</span>
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
    { key: 'blocked',  label: 'Blocked',  bg: 'bg-fail' },
    { key: 'external', label: 'External', bg: 'bg-info' },
    { key: 'at_risk',  label: 'At Risk',  bg: 'bg-warn' },
    { key: 'healthy',  label: 'Healthy',  bg: 'bg-ok' },
    { key: 'done',     label: 'Done',     bg: 'bg-ink-muted/50' },
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
            <span className="text-ink-muted">{label}</span>
            <span className="text-ink-secondary font-bold font-mono">{summary[key] ?? 0}</span>
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
          <ArrowRight className="w-3.5 h-3.5 text-fail" />
          <span className="text-sm font-medium text-ink">Impact Chain</span>
        </div>
        <span className="text-xs text-ink-muted font-mono">
          {impactResult.directly_affected.length}d + {impactResult.transitively_affected.length}t
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Directly affected */}
        <div>
          <p className="text-[11px] uppercase tracking-wide text-ink-muted mb-2">
            Directly affected ({impactResult.directly_affected.length})
          </p>
          {impactResult.directly_affected.length === 0 ? (
            <p className="text-xs text-ink-muted">None</p>
          ) : (
            <div className="space-y-1.5">
              {impactResult.directly_affected.map((entry) => (
                <div key={entry.entity_id} className="space-y-1">
                  <button
                    onClick={() => onHighlight(entry.entity_id)}
                    className="w-full flex items-center gap-2 text-left hover:bg-surface-700/50 rounded px-2 py-1 transition-colors group"
                  >
                    <StatusBadge status={entry.state} />
                    <span className="text-xs text-ink-secondary">{entry.name}</span>
                    <GitBranch className="w-3 h-3 text-ink-muted group-hover:text-acid ml-auto transition-colors" />
                  </button>
                  {entry.evidence_path.length > 0 && (
                    <div className="pl-2">
                      <EvidencePath path={entry.evidence_path} nameMap={nameMap} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Transitively affected */}
        <div>
          <p className="text-[11px] uppercase tracking-wide text-ink-muted mb-2">
            Transitively affected ({impactResult.transitively_affected.length})
          </p>
          {impactResult.transitively_affected.length === 0 ? (
            <p className="text-xs text-ink-muted">None</p>
          ) : (
            <div className="space-y-2">
              {impactResult.transitively_affected.map((entry) => (
                <div key={entry.entity_id} className="space-y-1">
                  <button
                    onClick={() => onHighlight(entry.entity_id)}
                    className="w-full flex items-center gap-2 text-left hover:bg-surface-700/50 rounded px-2 py-1 transition-colors group"
                  >
                    <StatusBadge status={entry.state} />
                    <span className="text-xs text-ink-secondary">{entry.name}</span>
                    <GitBranch className="w-3 h-3 text-ink-muted group-hover:text-acid ml-auto transition-colors" />
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
            <p className="text-[11px] uppercase tracking-wide text-ink-muted mb-2">
              Unaffected ({impactResult.unaffected.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {impactResult.unaffected.map((entry) => (
                <button
                  key={entry.entity_id}
                  onClick={() => onHighlight(entry.entity_id)}
                  title="Show on graph"
                  className="flex items-center gap-1 text-xs text-ink-muted bg-surface-800 border border-border rounded px-2 py-0.5 hover:border-acid/50 hover:text-ink-secondary transition-colors"
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
