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
        <div className="w-6 h-6 border-2 border-accent-blue border-t-transparent rounded-full animate-spin" />
        <span className="text-sm text-gray-500">Computing forecast…</span>
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
        <div className="flex items-start gap-3 p-4 bg-red-900/20 border border-red-900 rounded-lg">
          <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0" />
          <div>
            <p className="text-sm text-red-300 font-medium">Infeasible change set</p>
            {forecast.error && (
              <p className="text-xs text-red-500 mt-1 font-mono">[{forecast.error.code}] {forecast.error.message}</p>
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
    { name: 'Baseline',  length: baseLen,    fill: '#3b82f6' },
    { name: 'Forecast',  length: forecastLen, fill: delta > 0 ? '#f59e0b' : delta < 0 ? '#10b981' : '#6b7280' },
  ];

  return (
    <div className="space-y-6">
      {/* Simulated badge */}
      <div className="flex items-center gap-2 px-1">
        <span className="tag-simulated text-[10px]">SIMULATED</span>
        <span className="text-xs text-gray-600">
          Relative critical-path durations in <span className="font-mono text-gray-500">{unit}</span>. Not wall-clock dates.
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
        <p className="text-xs font-medium text-gray-400 mb-4">Critical-Path Length Comparison</p>
        <ResponsiveContainer width="100%" height={100}>
          <BarChart data={summaryData} layout="vertical" barCategoryGap="30%">
            <CartesianGrid strokeDasharray="3 3" stroke="#1a2440" horizontal={false} />
            <XAxis
              type="number"
              domain={[0, Math.max(baseLen, forecastLen) * 1.15]}
              tick={{ fill: '#6b7280', fontSize: 11, fontFamily: 'monospace' }}
              axisLine={{ stroke: '#1a2440' }}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: '#9ca3af', fontSize: 11, fontFamily: 'monospace' }}
              axisLine={false}
              tickLine={false}
              width={72}
            />
            <Tooltip
              contentStyle={{ background: '#0f1629', border: '1px solid #1a2440', borderRadius: 8, fontSize: 12, fontFamily: 'monospace' }}
              labelStyle={{ color: '#9ca3af' }}
              itemStyle={{ color: '#d1d5db' }}
              formatter={(v: number) => [`${v} ${unit}`, 'Length']}
            />
            <Bar dataKey="length" radius={[0, 4, 4, 0]}>
              {summaryData.map((entry, i) => (
                <Cell key={i} fill={entry.fill} />
              ))}
            </Bar>
            <ReferenceLine x={baseLen} stroke="#3b82f6" strokeDasharray="4 2" label={{ value: 'baseline', position: 'insideTopRight', fill: '#3b82f6', fontSize: 10, fontFamily: 'monospace' }} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Path membership chart */}
      <div className="card p-4">
        <p className="text-xs font-medium text-gray-400 mb-4">Critical-Path Membership</p>
        <ResponsiveContainer width="100%" height={Math.max(160, pathData.length * 36)}>
          <BarChart data={pathData} layout="vertical" barCategoryGap="20%">
            <CartesianGrid strokeDasharray="3 3" stroke="#1a2440" horizontal={false} />
            <XAxis
              type="number"
              domain={[0, 1]}
              ticks={[0, 1]}
              tickFormatter={(v) => v === 1 ? 'On path' : ''}
              tick={{ fill: '#6b7280', fontSize: 10, fontFamily: 'monospace' }}
              axisLine={{ stroke: '#1a2440' }}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="id"
              tick={{ fill: '#9ca3af', fontSize: 10, fontFamily: 'monospace' }}
              axisLine={false}
              tickLine={false}
              width={160}
            />
            <Tooltip
              contentStyle={{ background: '#0f1629', border: '1px solid #1a2440', borderRadius: 8, fontSize: 11, fontFamily: 'monospace' }}
              formatter={(v: number, name: string) => [v === 1 ? 'On path' : 'Off path', name]}
            />
            <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'monospace', color: '#9ca3af' }} />
            <Bar dataKey="Baseline" fill="#3b82f6" radius={[0, 3, 3, 0]} />
            <Bar dataKey="Forecast" fill="#10b981" radius={[0, 3, 3, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Change callout */}
      {delta !== 0 && (
        <div className={clsx(
          'flex items-start gap-3 p-3 rounded-lg border text-xs',
          delta > 0 ? 'bg-yellow-900/20 border-yellow-800 text-yellow-300' : 'bg-emerald-900/20 border-emerald-800 text-emerald-300',
        )}>
          <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <div>
            <span className="font-medium">{delta > 0 ? 'Delay added:' : 'Delay reduced:'} </span>
            <span>{Math.abs(delta)} {unit}</span>
            {delta > 0 && (
              <span className="text-gray-400 ml-1">
                — this option extends the critical path. Consider whether bypassing the dependency is worth the trade-off.
              </span>
            )}
          </div>
        </div>
      )}

      {/* Assumptions */}
      {forecast.limitations.length > 0 && (
        <div className="text-[10px] text-gray-700 space-y-0.5 border-t border-surface-700 pt-3">
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
      <p className="text-[10px] text-gray-600 uppercase tracking-wide">{label}</p>
      <p className={clsx(
        'font-mono font-bold',
        large ? 'text-2xl' : 'text-lg',
        highlight === 'warn'    ? 'text-yellow-400' :
        highlight === 'good'    ? 'text-emerald-400' :
        highlight === 'neutral' ? 'text-gray-400' :
        'text-gray-200',
      )}>
        {value}
      </p>
      <p className="text-[10px] text-gray-600">{unit}</p>
    </div>
  );
}
