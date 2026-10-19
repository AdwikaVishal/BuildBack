/**
 * MCPInspector — developer-facing MCP observability console.
 *
 * Three clearly separated areas:
 *  A. Connection summary — health state, endpoint, last check, counts.
 *     (A successful health check is NOT a claim that tool calls succeed.)
 *  B. Tool registry — live tools/list with filter, required args, and
 *     expandable input schemas (copy-to-clipboard).
 *  C. Tool call log — every invocation with status pill, duration, context
 *     IDs, and expandable arguments / structured results / error details.
 *
 * Honesty rules:
 *  - No fabricated metrics, latency, or timestamps; missing fields show
 *    "Not available".
 *  - Call results and errors are never truncated without an expand control.
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Terminal, RefreshCw, ChevronDown, ChevronUp,
  Clock, Copy, Check, Filter, Search, Circle, Braces, Layers, Radio,
} from 'lucide-react';
import {
  callLog,
  subscribeCalls,
  subscribeStatus,
  getConnectionStatus,
  checkConnection,
  listTools,
  type MCPToolCall,
} from '../api/mcpClient';
import type { MCPToolInfo } from '../types';
import { clsx } from 'clsx';

// ── Reference baseline (spec'd tools) — used ONLY to flag expected-but-missing
// tools. The list rendered below is always the live-discovered set from the
// MCP server, so new tools (e.g. Person 3's scheduling tool) appear without
// any frontend change.
const BASELINE_TOOLS = [
  'get_plan_state',
  'simulate_disruption',
  'analyze_blocked_tasks',
  'find_alternative_dependencies',
  'evaluate_recovery_plan',
  'rebalance_workload',
  'recalculate_delivery_forecast',
  'validate_hard_constraints',
  'compare_recovery_options',
  'find_substitute_options',
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
      className="p-1 rounded text-ink-muted hover:text-ink-secondary hover:bg-surface-700 transition-colors flex-shrink-0"
    >
      {copied ? <Check className="w-3 h-3 text-ok" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

// ── Pretty JSON ───────────────────────────────────────────────────────────────

function JsonBlock({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2);
  return (
    <div className="relative group">
      <pre className="bg-surface-900 border border-border rounded p-2.5 text-[11px] text-ink-secondary overflow-auto max-h-72 leading-relaxed">
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
    <div className={clsx('text-xs border-b border-border last:border-0', isError && 'bg-fail/10')}>
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-surface-700/40 text-left transition-colors"
        onClick={onToggle}
      >
        {/* Status pill — a green dot alone is easy to miss */}
        <span className={clsx(
          'flex-shrink-0 inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
          isPending ? 'bg-info/15 text-info'
          : isError ? 'bg-fail/15 text-fail'
          : 'bg-ok/15 text-ok',
        )}>
          <span className={clsx(
            'w-1.5 h-1.5 rounded-full',
            isPending ? 'bg-info animate-pulse' : isError ? 'bg-fail' : 'bg-ok',
          )} />
          {isPending ? 'running' : isError ? 'error' : 'ok'}
        </span>

        {/* Tool name */}
        <span className="font-mono text-xs text-ink flex-shrink-0 w-44 lg:w-56 truncate" title={call.tool}>
          {call.tool}
        </span>

        {/* IDs */}
        <div className="flex items-center gap-2 flex-1 min-w-0 overflow-hidden">
          {Object.entries(ids).slice(0, 2).map(([k, v]) => (
            <span key={k} className="text-ink-muted text-[11px] font-mono truncate">
              {k}: <span className="text-ink-secondary">{v}</span>
            </span>
          ))}
        </div>

        {/* Duration */}
        {call.durationMs != null && (
          <span className="flex items-center gap-1 text-ink-muted text-[11px] flex-shrink-0">
            <Clock className="w-3 h-3" />
            {call.durationMs}ms
          </span>
        )}

        {/* Error summary */}
        {isError && call.error && (
          <span className="text-fail text-[11px] truncate max-w-[200px] flex-shrink-0" title={call.error}>{call.error}</span>
        )}

        <span className="ml-2 flex-shrink-0 text-ink-muted">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-border/60">
          {/* IDs strip */}
          {Object.keys(ids).length > 0 && (
            <div className="flex flex-wrap gap-2 pt-2">
              {Object.entries(ids).map(([k, v]) => (
                <span key={k} className="text-[11px] font-mono bg-surface-700 border border-border rounded px-1.5 py-0.5 text-ink-secondary">
                  {k}: <span className="text-ink-secondary">{v}</span>
                </span>
              ))}
            </div>
          )}

          {/* Timing */}
          <div className="text-[11px] text-ink-muted font-mono">
            <span>{new Date(call.startedAt).toISOString()}</span>
            {call.completedAt && (
              <span> → {new Date(call.completedAt).toISOString()} ({call.durationMs}ms)</span>
            )}
          </div>

          {/* Arguments */}
          <div>
            <p className="text-[11px] text-ink-muted uppercase tracking-wide mb-1.5">Arguments</p>
            <JsonBlock value={call.args} />
          </div>

          {/* Response */}
          {call.result !== undefined && (
            <div>
              <p className="text-[11px] text-ink-muted uppercase tracking-wide mb-1.5">Response</p>
              <JsonBlock value={call.result} />
            </div>
          )}

          {/* Error */}
          {call.error && (
            <div>
              <p className="text-[11px] text-fail uppercase tracking-wide mb-1">Error</p>
              <p className="font-mono text-fail text-xs break-words">{call.error}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Discovered tool row (live from the MCP server) ───────────────────────────

function ToolRow({ tool, used }: { tool: MCPToolInfo; used: boolean }) {
  const [open, setOpen] = useState(false);
  const required = ((tool.input_schema as { required?: string[] })?.required) ?? [];
  const description = tool.description.split('\n')[0];

  return (
    <div className="border-b border-border/60 last:border-0">
      <button
        className="w-full flex items-center gap-2 py-2 px-1 text-left hover:bg-surface-700/30 rounded"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', used ? 'bg-info' : 'bg-ok')} />
        <span className="font-mono text-xs text-ink flex-shrink-0">{tool.name}</span>
        {required.length > 0 && (
          <span
            className="text-[11px] text-warn/90 flex-shrink-0 truncate max-w-[180px]"
            title={`required arguments: ${required.join(', ')}`}
          >
            requires: {required.join(', ')}
          </span>
        )}
        <span className="text-xs text-ink-muted truncate flex-1 hidden sm:inline" title={description}>
          {description}
        </span>
        {used && (
          <span className="text-[10px] bg-info/15 border border-info/40 rounded px-1.5 py-px text-info flex-shrink-0">used this session</span>
        )}
        {open ? <ChevronUp className="w-3.5 h-3.5 text-ink-muted flex-shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 text-ink-muted flex-shrink-0" />}
      </button>
      {open && (
        <div className="pb-3 px-1 space-y-1.5">
          {tool.description && (
            <p className="text-xs text-ink-muted leading-relaxed whitespace-pre-wrap">{tool.description}</p>
          )}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-ink-muted">
              <Braces className="w-3 h-3" /> Input schema
            </div>
            <CopyButton text={JSON.stringify(tool.input_schema, null, 2)} />
          </div>
          <pre className="bg-surface-900 border border-border rounded p-2.5 text-[11px] text-ink-secondary overflow-auto max-h-72 leading-relaxed">
            {JSON.stringify(tool.input_schema, null, 2)}
          </pre>
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
  const [toolSearch, setToolSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'success' | 'error' | 'pending'>('all');
  const [tools, setTools]         = useState<MCPToolInfo[]>([]);
  const [toolsError, setToolsError] = useState<string | null>(null);

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

  // Load the live tool list (names, descriptions, input schemas) from the
  // MCP server via the adapter. Refresh whenever the connection is checked.
  const refreshTools = useCallback(async () => {
    try {
      setTools(await listTools(true));
      setToolsError(null);
    } catch (err) {
      setTools([]);
      setToolsError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    // Fetch on mount; ignore results after unmount.
    let cancelled = false;
    listTools(true)
      .then((t) => { if (!cancelled) { setTools(t); setToolsError(null); } })
      .catch((err) => {
        if (!cancelled) {
          setTools([]);
          setToolsError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => { cancelled = true; };
  }, []);

  const handleCheck = useCallback(async () => {
    setChecking(true);
    await checkConnection();
    await refreshTools();
    setChecking(false);
  }, [refreshTools]);

  // Filtering
  const filtered = calls.filter((c) => {
    if (search && !c.tool.toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter === 'success' && (c.isError || !c.completedAt)) return false;
    if (statusFilter === 'error'   && !c.isError) return false;
    if (statusFilter === 'pending' && c.completedAt) return false;
    return true;
  });

  const filteredTools = tools.filter((t) =>
    !toolSearch || t.name.toLowerCase().includes(toolSearch.toLowerCase()),
  );

  // Missing baseline tools (reference only — the list itself is live).
  const discoveredNames = new Set(tools.map((t) => t.name));
  const missingBaseline = BASELINE_TOOLS.filter((n) => !discoveredNames.has(n));

  return (
    <div className="space-y-4">
      {/* ── A. Connection summary — health, endpoint, discovery counts ── */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-acid" />
            <span className="text-base font-semibold text-ink">MCP Connection</span>
            <span className={clsx(
              'badge',
              status.connected ? 'tag-healthy' : 'tag-blocked',
            )}>
              {status.connected ? 'CONNECTED' : 'DISCONNECTED'}
            </span>
          </div>
          <button
            className="btn-ghost text-xs"
            onClick={handleCheck}
            disabled={checking}
          >
            <RefreshCw className={clsx('w-3.5 h-3.5', checking && 'animate-spin')} />
            {checking ? 'Checking…' : 'Re-check health'}
          </button>
        </div>

        <div className="p-4 space-y-3">
          {/* Fact grid — only values that actually exist; missing = "Not available" */}
          <dl className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3 text-xs">
            <Fact label="Endpoint" value={<span className="font-mono">{status.serverUrl}</span>} />
            <Fact
              label="Server"
              value={status.serverName
                ? <span className="font-mono">{status.serverName}{status.serverVersion ? ` v${status.serverVersion}` : ''}</span>
                : <span className="text-ink-muted">Not available</span>}
            />
            <Fact
              label="Last health check"
              value={status.lastCheckedAt
                ? <span className="font-mono">{new Date(status.lastCheckedAt).toLocaleTimeString()}</span>
                : <span className="text-ink-muted">Not available</span>}
            />
            <Fact
              label="Discovered tools"
              value={tools.length > 0
                ? <span className="font-mono text-acid">{tools.length}</span>
                : status.connected
                  ? <span className="text-ink-muted">loading…</span>
                  : <span className="text-ink-muted">Not available</span>}
            />
            <Fact
              label="Calls this session"
              value={<span className="font-mono">{calls.length}</span>}
            />
            <Fact
              label="Assistant provider"
              value={status.assistantProvider
                ? <span className="font-mono">{status.assistantProvider}</span>
                : <span className="text-ink-muted">Not available</span>}
            />
          </dl>

          {status.error && (
            <p className="text-xs text-fail font-mono break-words">{status.error}</p>
          )}
          {!status.connected && (
            <p className="text-xs text-ink-muted leading-relaxed">
              Start <code className="font-mono text-ink-secondary">python -m rippleback.server</code>, then the
              adapter (<code className="font-mono text-ink-secondary">python -m rippleback.adapter</code>), then{' '}
              <code className="font-mono text-ink-secondary">npm run dev</code>.
              The dashboard shows no data while offline — it never fabricates results.
            </p>
          )}
        </div>
      </div>

      {/* ── B. Tool registry — live tools/list from the MCP server ── */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2 min-w-0">
            <Layers className="w-4 h-4 text-info flex-shrink-0" />
            <span className="text-base font-semibold text-ink">Tool Registry</span>
            <span className="text-xs text-ink-muted">
              {tools.length > 0
                ? `${tools.length} tools discovered live from the server`
                : toolsError ? `error: ${toolsError}` : (status.connected ? 'loading…' : 'offline — cannot discover')}
            </span>
          </div>
          {tools.length > 0 && (
            <div className="flex items-center gap-1.5 bg-surface-700 border border-border rounded px-2 py-1 flex-shrink-0">
              <Search className="w-3 h-3 text-ink-muted" />
              <input
                type="text"
                placeholder="Filter tools by name…"
                value={toolSearch}
                onChange={(e) => setToolSearch(e.target.value)}
                className="bg-transparent text-xs text-ink-secondary placeholder-ink-muted focus:outline-none w-40"
                aria-label="Filter tools by name"
              />
            </div>
          )}
        </div>

        {tools.length > 0 ? (
          <div className="max-h-[420px] overflow-y-auto px-2 divide-y divide-transparent">
            {filteredTools.length === 0 ? (
              <p className="text-xs text-ink-muted py-6 text-center">
                No tool matches “{toolSearch}”.
              </p>
            ) : (
              filteredTools.map((tool) => (
                <ToolRow
                  key={tool.name}
                  tool={tool}
                  used={calls.some((c) => c.tool === tool.name)}
                />
              ))
            )}
          </div>
        ) : (
          <div className="p-4">
            <p className="text-xs text-ink-muted leading-relaxed">
              The list comes from the MCP server's own <span className="font-mono text-ink-secondary">tools/list</span> —
              nothing is hardcoded, so newly registered tools appear here automatically.
            </p>
          </div>
        )}
        {status.connected && missingBaseline.length > 0 && (
          <p className="px-4 pb-3 text-[11px] text-warn/80">
            Expected but not currently registered: {missingBaseline.join(', ')}
          </p>
        )}
      </div>

      {/* ── C. Tool call log — every invocation with expandable args/results ─ */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-warn" />
            <span className="text-base font-semibold text-ink">Tool Call Log</span>
          </div>
          <span className="text-xs text-ink-muted">{calls.length} total · {filtered.length} shown</span>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border flex-wrap gap-y-2">
          {/* Search */}
          <div className="flex items-center gap-1.5 bg-surface-700 border border-border rounded px-2 py-1 flex-1 min-w-[160px]">
            <Search className="w-3 h-3 text-ink-muted" />
            <input
              type="text"
              placeholder="Filter by tool name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-transparent text-xs text-ink-secondary placeholder-ink-muted focus:outline-none flex-1"
            />
          </div>

          {/* Status filter */}
          <div className="flex items-center gap-1">
            <Filter className="w-3 h-3 text-ink-muted" />
            {(['all', 'success', 'error', 'pending'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setStatusFilter(f)}
                className={clsx(
                  'text-[11px] px-2 py-0.5 rounded transition-colors',
                  statusFilter === f
                    ? f === 'error' ? 'bg-fail/15 text-fail' :
                      f === 'success' ? 'bg-ok/15 text-ok' :
                      f === 'pending' ? 'bg-info/15 text-info' :
                      'bg-surface-500 text-ink-secondary'
                    : 'text-ink-muted hover:text-ink-secondary',
                )}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <Circle className="w-8 h-8 text-ink-muted" strokeWidth={1} />
            <p className="text-xs text-ink-muted">
              {calls.length === 0
                ? 'No tool calls yet. Ask the assistant or run a simulation to generate traffic.'
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

// ── Connection fact cell ──────────────────────────────────────────────────────

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="label-tech truncate mb-1">{label}</dt>
      <dd className="text-ink-secondary truncate">{value}</dd>
    </div>
  );
}
