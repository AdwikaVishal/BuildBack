import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { AlertCircle, Info } from 'lucide-react';
import type { ForecastResult } from '../types';
import { EmptyState } from './ui/EmptyState';
import { clsx } from 'clsx';

interface Props {
  forecast: ForecastResult | null;
  loading?: boolean;
}

export function TimelineChart({ forecast, loading }: Props) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-3">
        <div className="w-6 h-6 border-2 border-acid border-t-transparent rounded-full animate-spin" />
        <span className="text-sm text-ink-muted">Computing forecast…</span>
      </div>
    );
  }

  if (!forecast) {
    return (
      <EmptyState
        icon={Info}
        title="No forecast data"
        description="Select a recovery option to see the before/after timeline comparison."
      />
    );
  }

  if (!forecast.feasible) {
    return (
      <div className="p-6 space-y-3">
        <div className="flex items-start gap-3 p-4 bg-red-900/20 border border-fail/40 rounded-lg">
          <AlertCircle className="w-5 h-5 text-fail flex-shrink-0" />
          <div>
            <p className="text-sm text-fail font-medium">Infeasible change set</p>
            {forecast.error && (
              <p className="text-xs text-fail mt-1 font-mono">[{forecast.error.code}] {forecast.error.message}</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  const baseLen = forecast.baseline_length;
  const forecastLen = forecast.forecast_length ?? baseLen;
  const delta = forecast.delay_delta ?? 0;
  const unit = forecast.unit;

  // Build bar data: one row per node on either path
  const allNodes = Array.from(
    new Set([...forecast.baseline_path, ...(forecast.forecast_path ?? [])])
  );

  const pathData = allNodes.map((nodeId) => {
    const inBaseline  = forecast.baseline_path.includes(nodeId);
    const inForecast  = (forecast.forecast_path ?? []).includes(nodeId);
    return {
      id: nodeId,
      Baseline:  inBaseline  ? 1 : 0,
      Forecast:  inForecast  ? 1 : 0,
      changed:   inBaseline !== inForecast,
    };
  });

  // Summary bar chart data
  const summaryData = [
    { name: 'Baseline',  length: baseLen,    fill: '#75BFFF' },
    { name: 'Forecast',  length: forecastLen, fill: delta > 0 ? '#FFBF4A' : delta < 0 ? '#75E6A0' : '#737368' },
  ];

  return (
    <div className="space-y-6">
      {/* Simulated badge */}
      <div className="flex items-center gap-2 px-1">
        <span className="tag-simulated text-[11px]">SIMULATED</span>
        <span className="text-xs text-ink-muted">
          Relative critical-path durations in <span className="font-mono text-ink-muted">{unit}</span>. Not wall-clock dates.
        </span>
      </div>

      {/* Delta summary */}
      <div className="grid grid-cols-3 gap-3">
        <SummaryCard label="Baseline length" value={`${baseLen}`} unit={unit} />
        <SummaryCard
          label="Forecast length"
          value={`${forecastLen}`}
          unit={unit}
          highlight={delta > 0 ? 'warn' : delta < 0 ? 'good' : undefined}
        />
        <SummaryCard
          label="Δ Delay"
          value={`${delta >= 0 ? '+' : ''}${delta}`}
          unit={unit}
          highlight={delta > 0 ? 'warn' : delta < 0 ? 'good' : 'neutral'}
          large
        />
      </div>

      {/* Horizontal bar: baseline vs forecast length */}
      <div className="card p-4">
        <p className="text-sm font-medium text-ink-secondary mb-4">Critical-Path Length Comparison</p>
        <ResponsiveContainer width="100%" height={130}>
          <BarChart data={summaryData} layout="vertical" barCategoryGap="30%">
            <CartesianGrid strokeDasharray="3 3" stroke="#303027" horizontal={false} />
            <XAxis
              type="number"
              domain={[0, Math.max(baseLen, forecastLen) * 1.15]}
              tick={{ fill: '#737368', fontSize: 12, fontFamily: 'monospace' }}
              axisLine={{ stroke: '#303027' }}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: '#A3A397', fontSize: 13, fontFamily: 'monospace' }}
              axisLine={false}
              tickLine={false}
              width={100}
            />
            <Tooltip
              contentStyle={{ background: '#171713', border: '1px solid #303027', borderRadius: 8, fontSize: 12, fontFamily: 'monospace' }}
              labelStyle={{ color: '#A3A397' }}
              itemStyle={{ color: '#F7F7F0' }}
              formatter={(v: number) => [`${v} ${unit}`, 'Length']}
            />
            <Bar dataKey="length" radius={[0, 4, 4, 0]}>
              {summaryData.map((entry, i) => (
                <Cell key={i} fill={entry.fill} />
              ))}
            </Bar>
            <ReferenceLine x={baseLen} stroke="#75BFFF" strokeDasharray="4 2" label={{ value: 'baseline', position: 'insideTopRight', fill: '#75BFFF', fontSize: 11, fontFamily: 'monospace' }} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Path membership chart */}
      <div className="card p-4">
        <p className="text-sm font-medium text-ink-secondary mb-4">Critical-Path Membership</p>
        <ResponsiveContainer width="100%" height={Math.max(220, pathData.length * 42)}>
          <BarChart data={pathData} layout="vertical" barCategoryGap="20%">
            <CartesianGrid strokeDasharray="3 3" stroke="#303027" horizontal={false} />
            <XAxis
              type="number"
              domain={[0, 1]}
              ticks={[0, 1]}
              tickFormatter={(v) => v === 1 ? 'On path' : ''}
              tick={{ fill: '#737368', fontSize: 12, fontFamily: 'monospace' }}
              axisLine={{ stroke: '#303027' }}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="id"
              tick={{ fill: '#A3A397', fontSize: 12, fontFamily: 'monospace' }}
              axisLine={false}
              tickLine={false}
              width={220}
            />
            <Tooltip
              contentStyle={{ background: '#171713', border: '1px solid #303027', borderRadius: 8, fontSize: 12, fontFamily: 'monospace' }}
              formatter={(v: number, name: string) => [v === 1 ? 'On path' : 'Off path', name]}
            />
            <Legend wrapperStyle={{ fontSize: 12, fontFamily: 'monospace', color: '#A3A397' }} />
            <Bar dataKey="Baseline" fill="#75BFFF" radius={[0, 3, 3, 0]} />
            <Bar dataKey="Forecast" fill="#75E6A0" radius={[0, 3, 3, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Change callout */}
      {delta !== 0 && (
        <div className={clsx(
          'flex items-start gap-3 p-3 rounded-lg border text-xs',
          delta > 0 ? 'bg-warn/10 border-warn/40 text-warn' : 'bg-ok/10 border-ok/40 text-ok',
        )}>
          <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <div>
            <span className="font-medium">{delta > 0 ? 'Delay added:' : 'Delay reduced:'} </span>
            <span>{Math.abs(delta)} {unit}</span>
            {delta > 0 && (
              <span className="text-ink-secondary ml-1">
                — this option extends the critical path. Consider whether bypassing the dependency is worth the trade-off.
              </span>
            )}
          </div>
        </div>
      )}

      {/* Assumptions */}
      {forecast.limitations.length > 0 && (
        <div className="text-[11px] text-ink-muted space-y-0.5 border-t border-border pt-3">
          {forecast.limitations.slice(0, 3).map((l, i) => <p key={i}>{l}</p>)}
        </div>
      )}
    </div>
  );
}

// ── Small stat card ───────────────────────────────────────────────────────────

function SummaryCard({
  label,
  value,
  unit,
  highlight,
  large,
}: {
  label: string;
  value: string;
  unit: string;
  highlight?: 'warn' | 'good' | 'neutral';
  large?: boolean;
}) {
  return (
    <div className="card p-3 space-y-1">
      <p className="text-[11px] text-ink-muted uppercase tracking-wide">{label}</p>
      <p className={clsx(
        'font-mono font-bold',
        large ? 'text-2xl' : 'text-lg',
        highlight === 'warn'    ? 'text-warn' :
        highlight === 'good'    ? 'text-ok' :
        highlight === 'neutral' ? 'text-ink-secondary' :
        'text-ink',
      )}>
        {value}
      </p>
      <p className="text-[10px] text-ink-muted">{unit}</p>
    </div>
  );
}
