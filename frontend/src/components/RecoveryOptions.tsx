/**
 * RecoveryOptions — decision-making workspace for ranked recovery options.
 *
 * Improvements:
 *  - Human-readable option labels (parsed from drop-dependency:X->Y format)
 *  - Change summary (what edge is removed, what gets unblocked)
 *  - Before/after path diff in expanded view
 *  - Clear recommended treatment
 *  - Side-by-side compare mode
 *  - Infeasible options with violation reasons
 *  - Backend-only ranking — no frontend scoring
 */
import { useState } from 'react';
import {
  Trophy, BarChart2, Info, ChevronDown, ChevronUp,
  ArrowRight, XCircle, Sparkles,
} from 'lucide-react';
import type { ComparisonResult, RankedEntry, RecoveryResult } from '../types';
import { EmptyState } from './ui/EmptyState';
import { clsx } from 'clsx';
import { useApp } from '../store/appStore';

interface Props {
  comparisonResult: ComparisonResult | null;
  recoveryResult: RecoveryResult | null;
  loading?: boolean;
  error?: string | null;
}

/** Parse "drop-dependency:dependent->prerequisite" into readable text */
function parseOptionId(optionId: string): { dependent: string; prerequisite: string } | null {
  const m = optionId.match(/^drop-dependency:(.+)->(.+)$/);
  if (!m) return null;
  return { dependent: m[1], prerequisite: m[2] };
}

function OptionLabel({ optionId }: { optionId: string }) {
  const parsed = parseOptionId(optionId);
  if (!parsed) {
    return <span className="font-mono text-gray-300 truncate text-xs">{optionId}</span>;
  }
  return (
    <span className="text-xs text-gray-300">
      Remove dependency: <span className="font-mono text-blue-300">{parsed.dependent}</span>
      {' '}no longer requires{' '}
      <span className="font-mono text-red-300">{parsed.prerequisite}</span>
    </span>
  );
}

export function RecoveryOptions({ comparisonResult, recoveryResult, loading, error }: Props) {
  const [, dispatch] = useApp();
  const [compareMode, setCompareMode] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <span className="text-sm text-gray-500">Generating recovery options…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="m-4 p-4 bg-red-900/20 border border-red-900 rounded-lg text-xs text-red-400">
        {error}
      </div>
    );
  }

  if (!comparisonResult && !recoveryResult) {
    return (
      <EmptyState
        icon={BarChart2}
        title="No recovery options yet"
        description="Trigger a disruption to generate and rank recovery options."
      />
    );
  }

  const ranked      = comparisonResult?.ranked ?? [];
  const excluded    = comparisonResult?.excluded ?? [];
  const recommendation = comparisonResult?.recommendation;
  const baseline    = comparisonResult?.baseline;

  function handleSelect(optionId: string) {
    dispatch({ type: 'SELECT_OPTION', optionId });
    dispatch({ type: 'LOG_ACTIVITY', stage: 'option_selected', label: 'Option selected for review', status: 'done', detail: optionId });
    dispatch({ type: 'SET_VIEW', view: 'approval' });
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-1">
          <Trophy className="w-4 h-4 text-yellow-500" />
          <span className="text-sm font-semibold text-gray-200">Recovery Options</span>
          {comparisonResult && (
            <span className="text-xs text-gray-600">
              {comparisonResult.feasible_count} feasible · {comparisonResult.scorable_count} scorable
            </span>
          )}
        </div>
        {ranked.length >= 2 && (
          <button
            className={clsx('btn-ghost text-xs', compareMode && 'border-blue-600 text-blue-300 bg-blue-900/20')}
            onClick={() => setCompareMode(!compareMode)}
          >
            <BarChart2 className="w-3.5 h-3.5" />
            {compareMode ? 'List view' : 'Compare side-by-side'}
          </button>
        )}
      </div>

      {/* No-recovery outcome */}
      {comparisonResult?.outcome === 'no_feasible_recovery' && (
        <div className="flex items-start gap-3 p-3 bg-red-900/20 border border-red-900/50 rounded-lg">
          <XCircle className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-red-300">
            No feasible recovery option was found. All options were excluded due to constraint violations.
          </p>
        </div>
      )}

      {/* Recommended banner */}
      {recommendation && (
        <div className="flex items-start gap-3 p-3 bg-emerald-900/15 border border-emerald-800/60 rounded-lg">
          <Sparkles className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs space-y-0.5">
            <p className="text-emerald-300 font-semibold">Backend recommendation</p>
            <OptionLabel optionId={recommendation} />
          </div>
        </div>
      )}

      {/* Baseline info */}
      {baseline && (
        <div className="flex items-center gap-2 text-xs text-gray-600 px-1">
          <span>Baseline critical path:</span>
          <span className="font-mono text-gray-400">{baseline.path.join(' → ')}</span>
          <span className="ml-auto text-gray-600">length: <span className="text-gray-400 font-mono">{baseline.length} {baseline.unit}</span></span>
        </div>
      )}

      {/* Compare mode */}
      {compareMode && ranked.length >= 2 ? (
        <div className="grid grid-cols-2 gap-3">
          {ranked.slice(0, 4).map((entry) => (
            <CompareCard
              key={entry.option_id}
              entry={entry}
              isRecommended={entry.option_id === recommendation}
              baseline={baseline}
              onSelect={() => handleSelect(entry.option_id)}
            />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {ranked.map((entry) => (
            <OptionCard
              key={entry.option_id}
              entry={entry}
              isRecommended={entry.option_id === recommendation}
              expanded={expandedId === entry.option_id}
              onToggle={() => setExpandedId(expandedId === entry.option_id ? null : entry.option_id)}
              onSelect={() => handleSelect(entry.option_id)}
              baseline={baseline}
            />
          ))}

          {/* Excluded section */}
          {excluded.length > 0 && (
            <div className="pt-2">
              <p className="text-[10px] uppercase tracking-wider text-gray-600 mb-2 px-1">
                Excluded ({excluded.length} infeasible)
              </p>
              {excluded.map((entry) => (
                <ExcludedCard key={entry.option_id} entry={entry} />
              ))}
            </div>
          )}

          {ranked.length === 0 && excluded.length === 0 && (
            <EmptyState icon={Info} title="No options returned" description="The backend returned an empty option list." />
          )}
        </div>
      )}

      {/* Weights footnote */}
      {comparisonResult && <WeightsFootnote weights={comparisonResult.weights} />}
    </div>
  );
}

// ── Option card (list mode) ───────────────────────────────────────────────────

interface CardProps {
  entry: RankedEntry;
  isRecommended: boolean;
  expanded: boolean;
  onToggle: () => void;
  onSelect: () => void;
  baseline?: { length: number; unit: string } | null;
}

function OptionCard({ entry, isRecommended, expanded, onToggle, onSelect, baseline }: CardProps) {
  const delta = entry.forecast?.delay_delta;
  const forecastLen = entry.forecast?.forecast_length;
  const parsed = parseOptionId(entry.option_id);

  // Unblocks list from the recovery result (if available)
  const unblockMatch = entry.option_id.match(/^drop-dependency:(.+)->(.+)$/);

  return (
    <div className={clsx(
      'card border transition-colors',
      isRecommended ? 'border-emerald-700/60 bg-emerald-950/10' : 'border-surface-600',
    )}>
      {/* Summary row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-surface-700/30 transition-colors"
        onClick={onToggle}
      >
        {/* Rank */}
        <span className={clsx(
          'w-7 h-7 flex items-center justify-center rounded-full text-xs font-bold flex-shrink-0',
          entry.rank === 1 ? 'bg-amber-500/20 text-amber-300 ring-1 ring-amber-600/50' : 'bg-surface-600 text-gray-400',
        )}>
          {entry.rank != null ? `#${entry.rank}` : '—'}
        </span>

        {/* Label */}
        <div className="flex-1 min-w-0 space-y-0.5">
          {parsed ? (
            <p className="text-xs text-gray-300">
              Remove: <span className="font-mono text-blue-300">{parsed.dependent}</span>
              {' → '}<span className="font-mono text-red-300">{parsed.prerequisite}</span>
            </p>
          ) : (
            <p className="text-xs font-mono text-gray-400 truncate">{entry.option_id}</p>
          )}
          {isRecommended && (
            <span className="inline-flex items-center gap-0.5 text-[10px] text-emerald-400">
              <Sparkles className="w-2.5 h-2.5" /> Recommended
            </span>
          )}
        </div>

        {/* Score */}
        {entry.scorable && entry.score != null && (
          <div className="text-right flex-shrink-0 hidden sm:block">
            <p className="text-[9px] text-gray-600 uppercase">Score</p>
            <p className="text-xs font-mono text-gray-300">{entry.score.toFixed(2)}</p>
          </div>
        )}

        {/* Delay badge */}
        {delta != null && <DelayBadge delta={delta} unit={entry.forecast?.unit ?? ''} />}

        <span className="text-gray-600 flex-shrink-0">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-surface-600 px-4 py-4 space-y-4">
          {/* Change summary */}
          {unblockMatch && (
            <ChangeSummary dependent={unblockMatch[1]} prerequisite={unblockMatch[2]} />
          )}

          {/* Metrics grid */}
          {entry.forecast && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-gray-600 mb-2">Forecast comparison</p>
              <div className="grid grid-cols-3 gap-3 text-xs">
                <MetricCell label="Baseline" value={baseline ? `${baseline.length}` : '—'} unit={baseline?.unit ?? ''} />
                <MetricCell
                  label="Forecast"
                  value={forecastLen != null ? `${forecastLen}` : '—'}
                  unit={entry.forecast.unit}
                  highlight={delta != null && delta > 0 ? 'warn' : delta != null && delta < 0 ? 'good' : undefined}
                />
                <MetricCell
                  label="Δ Delay"
                  value={delta != null ? `${delta >= 0 ? '+' : ''}${delta}` : '—'}
                  unit={entry.forecast.unit}
                  highlight={delta != null && delta > 0 ? 'warn' : delta != null && delta < 0 ? 'good' : undefined}
                  large
                />
              </div>
            </div>
          )}

          {/* Before / after paths */}
          {entry.forecast?.baseline_path && entry.forecast.forecast_path && (
            <PathDiff
              baseline={entry.forecast.baseline_path}
              forecast={entry.forecast.forecast_path}
            />
          )}

          {/* Limitations (first 2) */}
          {entry.limitations.length > 0 && (
            <div className="space-y-1">
              {entry.limitations.slice(0, 2).map((l, i) => (
                <p key={i} className="text-[10px] text-gray-600 italic leading-relaxed">{l}</p>
              ))}
            </div>
          )}

          {/* CTA */}
          <div className="pt-1">
            <button className="btn-primary text-sm px-4 py-2" onClick={onSelect}>
              <ArrowRight className="w-4 h-4" />
              Review & Approve
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Compare card ──────────────────────────────────────────────────────────────

function CompareCard({ entry, isRecommended, baseline, onSelect }: {
  entry: RankedEntry;
  isRecommended: boolean;
  baseline?: { length: number; unit: string } | null;
  onSelect: () => void;
}) {
  const delta = entry.forecast?.delay_delta;
  const parsed = parseOptionId(entry.option_id);
  return (
    <div className={clsx(
      'card p-4 flex flex-col gap-3',
      isRecommended && 'border-emerald-700/60 bg-emerald-950/10',
    )}>
      <div className="flex items-center gap-2">
        {entry.rank != null && (
          <span className="text-xs font-bold text-amber-400">#{entry.rank}</span>
        )}
        {isRecommended && <span className="tag-healthy text-[9px]"><Sparkles className="w-2.5 h-2.5 inline mr-0.5" />REC</span>}
        {delta != null && <DelayBadge delta={delta} unit={entry.forecast?.unit ?? ''} />}
      </div>

      <div className="text-xs text-gray-400 space-y-0.5">
        {parsed ? (
          <>
            <p>Remove dep:</p>
            <p className="font-mono text-blue-300 text-[10px] break-all">{parsed.dependent}</p>
            <p className="text-gray-600">→ no longer requires</p>
            <p className="font-mono text-red-300 text-[10px] break-all">{parsed.prerequisite}</p>
          </>
        ) : (
          <p className="font-mono break-all">{entry.option_id}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <MetricCell label="Score" value={entry.score?.toFixed(2) ?? '—'} unit="" />
        <MetricCell label="Baseline" value={`${baseline?.length ?? '—'}`} unit={baseline?.unit ?? ''} />
        <MetricCell label="Forecast" value={`${entry.forecast?.forecast_length ?? '—'}`} unit={entry.forecast?.unit ?? ''} />
        <MetricCell
          label="Δ"
          value={delta != null ? `${delta >= 0 ? '+' : ''}${delta}` : '—'}
          unit={entry.forecast?.unit ?? ''}
          highlight={delta != null && delta > 0 ? 'warn' : 'good'}
        />
      </div>

      <button className="btn-primary text-xs mt-auto justify-center" onClick={onSelect}>
        Review & Approve
      </button>
    </div>
  );
}

// ── Excluded card ─────────────────────────────────────────────────────────────

function ExcludedCard({ entry }: { entry: RankedEntry }) {
  const [exp, setExp] = useState(false);
  const parsed = parseOptionId(entry.option_id);
  return (
    <div className="card border border-surface-600 opacity-60 mb-1">
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-700/30 transition-colors"
        onClick={() => setExp((v) => !v)}
      >
        <XCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />
        <span className="text-xs text-gray-500 flex-1 min-w-0">
          {parsed
            ? `Remove: ${parsed.dependent} → ${parsed.prerequisite}`
            : <span className="font-mono truncate">{entry.option_id}</span>
          }
        </span>
        <span className="text-[10px] text-red-500 flex-shrink-0">infeasible</span>
        {exp ? <ChevronUp className="w-3.5 h-3.5 text-gray-600" /> : <ChevronDown className="w-3.5 h-3.5 text-gray-600" />}
      </button>
      {exp && entry.violations.length > 0 && (
        <div className="px-4 pb-3 space-y-1 border-t border-surface-700">
          {entry.violations.map((v, i) => (
            <p key={i} className="text-[10px] text-red-500 font-mono">[{v.code}] {v.message}</p>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Change summary ────────────────────────────────────────────────────────────

function ChangeSummary({ dependent, prerequisite }: { dependent: string; prerequisite: string }) {
  return (
    <div className="bg-surface-700/50 border border-surface-600 rounded-lg p-3 space-y-2">
      <p className="text-[10px] uppercase tracking-wide text-gray-600">Change summary</p>
      <div className="flex items-center gap-2 text-xs">
        <span className="tag-blocked text-[10px]">Remove edge</span>
        <span className="font-mono text-blue-300">{dependent}</span>
        <ArrowRight className="w-3.5 h-3.5 text-gray-600 flex-shrink-0" />
        <span className="font-mono text-red-300">{prerequisite}</span>
      </div>
      <p className="text-[10px] text-gray-500 leading-relaxed">
        <strong className="text-gray-400">{dependent}</strong> will no longer require{' '}
        <strong className="text-gray-400">{prerequisite}</strong> to complete before it can proceed.
        Downstream work blocked by this path may be unblocked.
      </p>
      <p className="text-[10px] text-amber-600 italic">
        Structural only — business readiness is not assessed.
      </p>
    </div>
  );
}

// ── Path diff ─────────────────────────────────────────────────────────────────

function PathDiff({ baseline, forecast }: { baseline: string[]; forecast: string[] }) {
  const baseSet = new Set(baseline);
  const foreSet = new Set(forecast);
  const removed = baseline.filter((id) => !foreSet.has(id));
  const added   = forecast.filter((id) => !baseSet.has(id));

  return (
    <div className="space-y-2">
      <p className="text-[10px] uppercase tracking-wide text-gray-600">Critical-path changes</p>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <p className="text-[9px] text-gray-600">Baseline path</p>
          <p className="text-[10px] font-mono text-gray-400 leading-relaxed">{baseline.join(' → ')}</p>
        </div>
        <div className="space-y-1">
          <p className="text-[9px] text-gray-600">Forecast path</p>
          <p className="text-[10px] font-mono text-gray-400 leading-relaxed">{forecast.join(' → ')}</p>
        </div>
      </div>
      {(removed.length > 0 || added.length > 0) && (
        <div className="flex gap-4 text-[10px]">
          {removed.length > 0 && (
            <span className="text-red-400">
              − removed: {removed.map((id) => <span key={id} className="font-mono">{id}</span>).reduce((a, b) => <>{a}, {b}</>)}
            </span>
          )}
          {added.length > 0 && (
            <span className="text-emerald-400">
              + added: {added.map((id) => <span key={id} className="font-mono">{id}</span>).reduce((a, b) => <>{a}, {b}</>)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function MetricCell({ label, value, unit, highlight, large }: {
  label: string; value: string; unit: string;
  highlight?: 'warn' | 'good'; large?: boolean;
}) {
  return (
    <div className="bg-surface-700/50 rounded p-2">
      <p className="text-[9px] text-gray-600 uppercase tracking-wide">{label}</p>
      <p className={clsx(
        'font-mono font-bold',
        large ? 'text-xl' : 'text-sm',
        highlight === 'warn' ? 'text-yellow-400' : highlight === 'good' ? 'text-emerald-400' : 'text-gray-200',
      )}>
        {value}
      </p>
      {unit && <p className="text-[9px] text-gray-700">{unit}</p>}
    </div>
  );
}

function DelayBadge({ delta, unit }: { delta: number; unit: string }) {
  if (delta === 0) return <span className="badge bg-surface-600 text-gray-500 border border-surface-500 text-[10px]">Δ 0</span>;
  return (
    <span className={clsx(
      'badge text-[10px] font-mono',
      delta > 0 ? 'bg-yellow-900/40 text-yellow-300 border border-yellow-800' : 'bg-emerald-900/40 text-emerald-300 border border-emerald-800',
    )}>
      {delta > 0 ? '+' : ''}{delta} {unit}
    </span>
  );
}

function WeightsFootnote({ weights }: { weights: ComparisonResult['weights'] }) {
  const active = Object.entries(weights).filter(([, v]) => v !== 0);
  return (
    <p className="text-[10px] text-gray-700 border-t border-surface-700 pt-2 px-1">
      Ranking by backend score. Active weights: {active.map(([k, v]) => `${k}=${v}`).join(', ')}.
      Lower score = better. Scores compare options within this call only.
    </p>
  );
}

