/**
 * MCPInspector — developer-facing MCP tool call monitor.
 *
 * Improvements:
 *  - Copy-to-clipboard for args + response JSON
 *  - Expected tool list vs actually-discovered tools (with gap indicators)
 *  - Filter by tool name and status (success / error / pending)
 *  - Formatted JSON rendering (syntax-highlighted via CSS classes)
 *  - Plan/simulation IDs displayed where present
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Terminal, Wifi, WifiOff, RefreshCw, ChevronDown, ChevronUp,
  Clock, Copy, Check, Filter, Search, Circle,
} from 'lucide-react';
import {
  callLog,
  subscribeCalls,
  subscribeStatus,
  getConnectionStatus,
  checkConnection,
  type MCPToolCall,
} from '../api/mcpClient';
import { clsx } from 'clsx';

// ── Expected tool list per the RippleBack spec ────────────────────────────────

const EXPECTED_TOOLS = [
  { name: 'get_plan_state',                  desc: 'Committed plan state' },
  { name: 'simulate_disruption',             desc: 'Hypothetical impact analysis' },
  { name: 'analyze_blocked_tasks',           desc: 'Blocker triage (BuildBack)' },
  { name: 'find_alternative_dependencies',   desc: 'Recovery alternatives' },
  { name: 'evaluate_recovery_plan',          desc: 'Structural evaluation' },
  { name: 'rebalance_workload',              desc: 'Workload reassignment' },
  { name: 'recalculate_delivery_forecast',   desc: 'Critical-path forecast' },
  { name: 'validate_hard_constraints',       desc: 'Constraint validation' },
  { name: 'compare_recovery_options',        desc: 'Scoring & ranking' },
  { name: 'find_substitute_options',         desc: 'Substitute candidates' },
];

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation();
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    }).catch(() => { /* clipboard unavailable */ });
  }

  return (
    <button
      onClick={handleCopy}
      title="Copy to clipboard"
      className="p-1 rounded text-gray-600 hover:text-gray-300 hover:bg-surface-600 transition-colors flex-shrink-0"
    >
      {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

// ── Pretty JSON ───────────────────────────────────────────────────────────────

function JsonBlock({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2);
  return (
    <div className="relative group">
      <pre className="bg-surface-900 border border-surface-600 rounded p-2.5 text-[10px] text-gray-400 overflow-x-auto max-h-56 leading-relaxed">
        {text}
      </pre>
      <div className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <CopyButton text={text} />
      </div>
    </div>
  );
}

// ── ID extractor (plan_id, scenario_id, evaluation_id, etc.) ─────────────────

function extractIds(obj: unknown): Record<string, string> {
  if (!obj || typeof obj !== 'object') return {};
  const result: Record<string, string> = {};
  const keys = ['plan_id', 'scenario_id', 'evaluation_id', 'plan_version', 'option_id'];
  for (const key of keys) {
    const val = (obj as Record<string, unknown>)[key];
    if (val != null) result[key] = String(val);
  }
  return result;
}

// ── Call row ──────────────────────────────────────────────────────────────────

function CallRow({ call, expanded, onToggle }: {
  call: MCPToolCall;
  expanded: boolean;
  onToggle: () => void;
}) {
  const isPending = !call.completedAt;
  const isError   = call.isError;
  const ids = extractIds(call.result);

  return (
    <div className={clsx('text-xs border-b border-surface-700 last:border-0', isError && 'bg-red-950/20')}>
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-surface-700/40 text-left transition-colors"
        onClick={onToggle}
      >
        {/* Status indicator */}
        <span className={clsx(
          'w-2 h-2 rounded-full flex-shrink-0',
          isPending ? 'bg-blue-400 animate-pulse' : isError ? 'bg-red-500' : 'bg-emerald-500',
        )} />

        {/* Tool name */}
        <span className="font-mono text-gray-300 flex-shrink-0 w-52 truncate" title={call.tool}>
          {call.tool}
        </span>

        {/* IDs */}
        <div className="flex items-center gap-2 flex-1 min-w-0 overflow-hidden">
          {Object.entries(ids).slice(0, 2).map(([k, v]) => (
            <span key={k} className="text-gray-700 text-[10px] font-mono truncate">
              {k}: <span className="text-gray-500">{v}</span>
            </span>
          ))}
        </div>

        {/* Duration */}
        {call.durationMs != null && (
          <span className="flex items-center gap-1 text-gray-600 flex-shrink-0">
            <Clock className="w-3 h-3" />
            {call.durationMs}ms
          </span>
        )}

        {/* Error summary */}
        {isError && call.error && (
          <span className="text-red-500 truncate max-w-[160px] flex-shrink-0">{call.error}</span>
        )}

        <span className="ml-2 flex-shrink-0 text-gray-600">
          {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-surface-700/60">
          {/* IDs strip */}
          {Object.keys(ids).length > 0 && (
            <div className="flex flex-wrap gap-2 pt-2">
              {Object.entries(ids).map(([k, v]) => (
                <span key={k} className="text-[10px] font-mono bg-surface-700 border border-surface-600 rounded px-1.5 py-0.5 text-gray-400">
                  {k}: <span className="text-gray-300">{v}</span>
                </span>
              ))}
            </div>
          )}

          {/* Timing */}
          <div className="text-[10px] text-gray-700 font-mono">
            <span>{new Date(call.startedAt).toISOString()}</span>
            {call.completedAt && (
              <span> → {new Date(call.completedAt).toISOString()} ({call.durationMs}ms)</span>
            )}
          </div>

          {/* Arguments */}
          <div>
            <p className="text-[10px] text-gray-600 uppercase tracking-wide mb-1.5">Arguments</p>
            <JsonBlock value={call.args} />
          </div>

          {/* Response */}
          {call.result !== undefined && (
            <div>
              <p className="text-[10px] text-gray-600 uppercase tracking-wide mb-1.5">Response</p>
              <JsonBlock value={call.result} />
            </div>
          )}

          {/* Error */}
          {call.error && (
            <div>
              <p className="text-[10px] text-red-600 uppercase tracking-wide mb-1">Error</p>
              <p className="font-mono text-red-400">{call.error}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function MCPInspector() {
  const [calls, setCalls]         = useState<MCPToolCall[]>([...callLog]);
  const [status, setStatus]       = useState(getConnectionStatus());
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [checking, setChecking]   = useState(false);
  const [search, setSearch]       = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'success' | 'error' | 'pending'>('all');

  useEffect(() => {
    const u1 = subscribeCalls((call) => {
      setCalls((prev) => {
        const idx = prev.findIndex((c) => c.id === call.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = call;
          return next;
        }
        return [call, ...prev].slice(0, 150);
      });
    });
    const u2 = subscribeStatus((s) => setStatus(s));
    return () => { u1(); u2(); };
  }, []);

  const handleCheck = useCallback(async () => {
    setChecking(true);
    await checkConnection();
    setChecking(false);
  }, []);

  // Filtering
  const filtered = calls.filter((c) => {
    if (search && !c.tool.toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter === 'success' && (c.isError || !c.completedAt)) return false;
    if (statusFilter === 'error'   && !c.isError) return false;
    if (statusFilter === 'pending' && c.completedAt) return false;
    return true;
  });

  // Discovered set
  const discoveredTools = new Set(status.tools ?? []);

  return (
    <div className="space-y-4">
      {/* Connection card */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-medium text-gray-300">MCP Inspector</span>
          </div>
          <button
            className="btn-ghost text-xs"
            onClick={handleCheck}
            disabled={checking}
          >
            <RefreshCw className={clsx('w-3.5 h-3.5', checking && 'animate-spin')} />
            {checking ? 'Checking…' : 'Ping'}
          </button>
        </div>

        <div className="p-4 space-y-4">
          {/* Banner */}
          <div className={clsx(
            'flex items-start gap-3 p-3 rounded-lg border text-xs',
            status.connected ? 'bg-emerald-900/15 border-emerald-800/60' : 'bg-red-900/15 border-red-900/50',
          )}>
            {status.connected
              ? <Wifi className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
              : <WifiOff className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />
            }
            <div className="space-y-1 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={clsx('font-semibold', status.connected ? 'text-emerald-300' : 'text-red-300')}>
                  {status.connected ? 'Connected' : 'Disconnected'}
                </span>
                <span className="text-gray-600 font-mono text-[10px]">{status.serverUrl}</span>
                {status.connected && status.serverName && (
                  <span className="text-gray-500 font-mono text-[10px]">
                    {status.serverName}{status.serverVersion ? ` v${status.serverVersion}` : ''}
                  </span>
                )}
              </div>
              {status.error && <p className="text-red-500">{status.error}</p>}
              {!status.connected && (
                <p className="text-gray-600">
                  Start <code className="font-mono text-gray-500">rippleback-mcp</code> then{' '}
                  <code className="font-mono text-gray-500">node proxy-server.cjs</code>.
                  Dashboard uses mock data while offline.
                </p>
              )}
              {status.lastCheckedAt && (
                <p className="text-gray-700 text-[10px]">
                  Last checked: {new Date(status.lastCheckedAt).toLocaleTimeString()}
                </p>
              )}
            </div>
          </div>

          {/* Tools matrix */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] uppercase tracking-wider text-gray-600">Tool availability</p>
              <span className="text-[10px] text-gray-600">
                {status.connected ? `${discoveredTools.size} discovered` : 'offline'}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-1">
              {EXPECTED_TOOLS.map(({ name, desc }) => {
                const discovered = discoveredTools.has(name);
                const used       = calls.some((c) => c.tool === name);
                return (
                  <div key={name} className="flex items-center gap-2 py-1">
                    <span className={clsx(
                      'w-1.5 h-1.5 rounded-full flex-shrink-0',
                      !status.connected ? 'bg-gray-700' :
                      discovered ? 'bg-emerald-500' : 'bg-red-700',
                    )} />
                    <span className="font-mono text-[10px] text-gray-400 flex-1">{name}</span>
                    <span className="text-[10px] text-gray-700 hidden sm:inline">{desc}</span>
                    {used && (
                      <span className="text-[9px] bg-blue-900/40 border border-blue-800 rounded px-1 text-blue-400">used</span>
                    )}
                    {status.connected && !discovered && (
                      <span className="text-[9px] text-red-600">not found</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Call log */}
      <div className="card">
        <div className="card-header">
          <span className="text-sm font-medium text-gray-300">Tool Call Log</span>
          <span className="text-xs text-gray-600">{calls.length} total · {filtered.length} shown</span>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-600 flex-wrap gap-y-2">
          {/* Search */}
          <div className="flex items-center gap-1.5 bg-surface-700 border border-surface-500 rounded px-2 py-1 flex-1 min-w-[160px]">
            <Search className="w-3 h-3 text-gray-600" />
            <input
              type="text"
              placeholder="Filter by tool name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-transparent text-xs text-gray-300 placeholder-gray-600 focus:outline-none flex-1"
            />
          </div>

          {/* Status filter */}
          <div className="flex items-center gap-1">
            <Filter className="w-3 h-3 text-gray-600" />
            {(['all', 'success', 'error', 'pending'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setStatusFilter(f)}
                className={clsx(
                  'text-[10px] px-2 py-0.5 rounded transition-colors',
                  statusFilter === f
                    ? f === 'error' ? 'bg-red-900/40 text-red-300' :
                      f === 'success' ? 'bg-emerald-900/40 text-emerald-300' :
                      f === 'pending' ? 'bg-blue-900/40 text-blue-300' :
                      'bg-surface-500 text-gray-300'
                    : 'text-gray-600 hover:text-gray-400',
                )}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <Circle className="w-8 h-8 text-gray-700" strokeWidth={1} />
            <p className="text-xs text-gray-600">
              {calls.length === 0
                ? 'No tool calls yet. Trigger a disruption to generate traffic.'
                : 'No calls match the current filter.'}
            </p>
          </div>
        ) : (
          <div className="divide-y divide-surface-700 max-h-[600px] overflow-y-auto">
            {filtered.map((call) => (
              <CallRow
                key={call.id}
                call={call}
                expanded={expandedId === call.id}
                onToggle={() => setExpandedId(expandedId === call.id ? null : call.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
