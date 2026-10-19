/**
 * DependencyGraph — interactive React Flow dependency visualisation.
 *
 * Refinement notes:
 *  - Larger, readable node cards (≥188px wide, 13px labels, 11px badges)
 *  - Fit-to-screen caps zoom at 1× so nodes are never shrunk to dots;
 *    manual zoom/pan stays in control (no repeated auto-fit)
 *  - Graph controls: fit, zoom ±, reset layout, center-selected, minimap
 *    toggle, hide-done toggle, filter by state
 *  - Compact 168×112 minimap with a lime viewport mask, toggleable
 *  - Collapsible legend; node detail panel with deps, evidence, forecast
 *  - Highlighted entity (from ImpactPanel) is selected AND centred once
 *  - Status labels always visible (not colour-only)
 */
import { useCallback, useMemo, useState, useEffect } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  ReactFlowProvider,
  type Node,
  type Edge,
  type NodeTypes,
  Handle,
  Position,
  BackgroundVariant,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { clsx } from 'clsx';
import type { Scenario, TriageResult, TriageState, EntityStatus, Entity } from '../types';
import { StatusBadge } from './ui/StatusBadge';
import {
  X, ExternalLink, Clock, Layers, Maximize2, ZoomIn, ZoomOut,
  RotateCcw, EyeOff, Eye, Filter, Crosshair, Map, ChevronDown, ChevronUp,
} from 'lucide-react';
import { useApp } from '../store/appStore';

// ── Colour helpers ────────────────────────────────────────────────────────────

function stateColor(state: TriageState | EntityStatus): { bg: string; border: string; text: string } {
  const map: Record<string, { bg: string; border: string; text: string }> = {
    blocked:     { bg: '#1C1111', border: '#FF6464', text: '#FFB3B3' },
    external:    { bg: '#0F1620', border: '#75BFFF', text: '#B9DCFF' },
    at_risk:     { bg: '#1B160B', border: '#FFBF4A', text: '#FFDFA6' },
    healthy:     { bg: '#0E1A12', border: '#75E6A0', text: '#A9EFC5' },
    done:        { bg: '#141412', border: '#3E3E32', text: '#737368' },
    pending:     { bg: '#161613', border: '#3E3E32', text: '#A3A397' },
    in_progress: { bg: '#0F1520', border: '#56A8E0', text: '#A9D3F5' },
  };
  return map[state] ?? { bg: '#171713', border: '#303027', text: '#A3A397' };
}

// ── Custom node ───────────────────────────────────────────────────────────────

interface NodeData {
  entity: Entity;
  triageState?: TriageState;
  isCriticalPath: boolean;
  isOnEvidencePath: boolean;
  isDisrupted: boolean;
  isHighlighted: boolean;
  onSelect: (entity: Entity, triageState?: TriageState) => void;
}

function EntityNode({ data }: { data: NodeData }) {
  const { entity, triageState, isCriticalPath, isOnEvidencePath, isDisrupted, isHighlighted, onSelect } = data;
  const displayState = triageState ?? entity.status;
  const colors = stateColor(displayState);

  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: colors.border, border: 'none', width: 8, height: 8 }}
      />
      <div
        className={clsx(
          'relative px-3.5 py-3 rounded-lg cursor-pointer transition-all select-none',
          'border shadow-lg min-w-[188px] max-w-[230px]',
          isCriticalPath && 'ring-1 ring-warn/60',
          isOnEvidencePath && 'brightness-125',
          isHighlighted && 'ring-2 ring-acid/80',
        )}
        style={{
          background: colors.bg,
          borderColor: isDisrupted ? '#FF6464' : colors.border,
        }}
        onClick={() => onSelect(entity, triageState)}
        title={`${entity.name} — ${displayState.replace('_', ' ')}`}
      >
        {/* Critical path pip */}
        {isCriticalPath && (
          <div className="absolute -top-1.5 -right-1.5 w-2.5 h-2.5 rounded-full bg-warn" />
        )}
        {/* Disrupted marker */}
        {isDisrupted && (
          <div className="absolute inset-0 rounded-lg border-2 border-fail animate-pulse opacity-40 pointer-events-none" />
        )}

        {/* Name */}
        <div className="text-[13px] font-semibold leading-tight mb-2" style={{ color: colors.text }}>
          {entity.name}
        </div>

        {/* Badges row */}
        <div className="flex items-center gap-1 flex-wrap">
          <StatusBadge status={displayState} showDot />
          {entity.metadata?.external && (
            <span className="flex items-center gap-0.5 text-[10px] text-info bg-info/10 border border-info/40 rounded px-1 py-px">
              <ExternalLink className="w-2.5 h-2.5" />EXT
            </span>
          )}
          {entity.metadata?.slack_minutes != null && (
            <span className="flex items-center gap-0.5 text-[10px] text-ink-secondary bg-surface-900/60 border border-surface-700 rounded px-1 py-px">
              <Clock className="w-2.5 h-2.5" />{entity.metadata.slack_minutes}m
            </span>
          )}
          {entity.duration != null && (
            <span className="flex items-center gap-0.5 text-[10px] text-acid bg-acid/5 border border-acid/30 rounded px-1 py-px">
              <Layers className="w-2.5 h-2.5" />d={entity.duration}
            </span>
          )}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        style={{ background: colors.border, border: 'none', width: 8, height: 8 }}
      />
    </>
  );
}

const NODE_TYPES: NodeTypes = { entity: EntityNode };

// ── Layout ────────────────────────────────────────────────────────────────────

function computeLayout(
  entities: Entity[],
  dependencies: Array<{ dependent: string; prerequisite: string }>,
): Record<string, { x: number; y: number }> {
  // Gaps sized for the larger node cards (≈190–230px wide, ~72px tall).
  const COL_GAP = 290;
  const ROW_GAP = 128;

  const inDeg: Record<string, number> = {};
  const adj:   Record<string, string[]> = {};
  for (const e of entities) { inDeg[e.id] = 0; adj[e.id] = []; }
  for (const d of dependencies) {
    if (inDeg[d.dependent] !== undefined) inDeg[d.dependent]++;
    if (adj[d.prerequisite])              adj[d.prerequisite].push(d.dependent);
  }

  const layer: Record<string, number> = {};
  const queue = entities.filter((e) => inDeg[e.id] === 0).map((e) => e.id);
  for (const id of queue) layer[id] = 0;

  while (queue.length) {
    const cur = queue.shift()!;
    for (const dep of (adj[cur] ?? [])) {
      inDeg[dep]--;
      layer[dep] = Math.max(layer[dep] ?? 0, (layer[cur] ?? 0) + 1);
      if (inDeg[dep] === 0) queue.push(dep);
    }
  }

  const cols: Record<number, string[]> = {};
  for (const [id, col] of Object.entries(layer)) {
    cols[col] = [...(cols[col] ?? []), id];
  }

  const positions: Record<string, { x: number; y: number }> = {};
  for (const [col, ids] of Object.entries(cols)) {
    const colNum = Number(col);
    ids.forEach((id, row) => {
      positions[id] = {
        x: colNum * COL_GAP + 24,
        y: row * ROW_GAP + 24 - ((ids.length - 1) * ROW_GAP) / 2 + 280,
      };
    });
  }
  entities.forEach((e, i) => {
    if (!positions[e.id]) positions[e.id] = { x: 24, y: i * ROW_GAP + 24 };
  });

  return positions;
}

// ── Detail panel ──────────────────────────────────────────────────────────────

interface DetailPanelProps {
  entity: Entity;
  triageState?: TriageState;
  triageEntry?: TriageResult['states'][0];
  scenario: Scenario;
  comparisonForecast?: { baseline_length: number; forecast_length: number | null; delay_delta: number | null; unit: string } | null;
  onClose: () => void;
}

function NodeDetailPanel({ entity, triageState, triageEntry, scenario, comparisonForecast, onClose }: DetailPanelProps) {
  // Deps where this entity is the prerequisite (outgoing)
  const outgoing = scenario.dependencies.filter((d) => d.prerequisite === entity.id);
  // Deps where this entity is the dependent (incoming)
  const incoming = scenario.dependencies.filter((d) => d.dependent === entity.id);
  // Entity name lookup
  const nameMap = Object.fromEntries(scenario.entities.map((e) => [e.id, e.name]));

  return (
    <div className="absolute top-3 right-3 z-20 w-[86vw] max-w-[352px] card shadow-2xl overflow-hidden">
      <div className="card-header">
        <div className="flex items-center gap-2 min-w-0">
          <StatusBadge status={triageState ?? entity.status} />
          <span className="text-[15px] font-semibold text-ink truncate">{entity.name}</span>
        </div>
        <button className="text-ink-muted hover:text-ink flex-shrink-0 ml-2" onClick={onClose} aria-label="Close details">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="p-4 space-y-3.5 text-[13px] overflow-y-auto max-h-[65vh]">
        {/* Identity */}
        <Grid2>
          <Cell k="ID"       v={entity.id} mono />
          <Cell k="Status"   v={triageState ?? entity.status} />
          {entity.duration  != null && <Cell k="Duration"  v={`${entity.duration} units`} />}
          {entity.assignee  != null && <Cell k="Assignee"  v={entity.assignee} />}
          {entity.effort    != null && <Cell k="Effort"    v={`${entity.effort} wu`} />}
          {entity.metadata?.slack_minutes != null && <Cell k="Slack" v={`${entity.metadata.slack_minutes} min`} />}
          {entity.metadata?.external === true && <Cell k="External" v="yes" highlight="warn" />}
        </Grid2>

        {/* Triage reason */}
        {triageEntry?.reason && (
          <Section title="Triage reason">
            <p className="text-ink-secondary leading-relaxed">{triageEntry.reason}</p>
          </Section>
        )}

        {/* Evidence path */}
        {triageEntry && triageEntry.evidence_path.length > 0 && (
          <Section title="Evidence path">
            <p className="font-mono text-ink-secondary text-[11px] leading-relaxed">
              {triageEntry.evidence_path.map((id) => nameMap[id] ?? id).join(' → ')}
            </p>
          </Section>
        )}

        {/* Incoming deps */}
        {incoming.length > 0 && (
          <Section title={`Requires (${incoming.length})`}>
            {incoming.map((d, i) => (
              <DepRow key={i} id={d.prerequisite} name={nameMap[d.prerequisite]} type={d.dependency_type} direction="in" />
            ))}
          </Section>
        )}

        {/* Outgoing deps */}
        {outgoing.length > 0 && (
          <Section title={`Required by (${outgoing.length})`}>
            {outgoing.map((d, i) => (
              <DepRow key={i} id={d.dependent} name={nameMap[d.dependent]} type={d.dependency_type} direction="out" />
            ))}
          </Section>
        )}

        {/* Forecast delta */}
        {comparisonForecast && (
          <Section title="Forecast (selected option)">
            <div className="grid grid-cols-3 gap-2">
              <MiniStat label="Baseline" value={`${comparisonForecast.baseline_length}`} unit={comparisonForecast.unit} />
              <MiniStat label="Forecast" value={comparisonForecast.forecast_length != null ? `${comparisonForecast.forecast_length}` : '—'} unit={comparisonForecast.unit} />
              <MiniStat
                label="Δ Delay"
                value={comparisonForecast.delay_delta != null ? `${comparisonForecast.delay_delta >= 0 ? '+' : ''}${comparisonForecast.delay_delta}` : '—'}
                unit={comparisonForecast.unit}
                highlight={comparisonForecast.delay_delta != null && comparisonForecast.delay_delta > 0 ? 'warn' : 'good'}
              />
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}

// Small sub-components for the detail panel
function Grid2({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-3 gap-y-1">{children}</div>;
}
function Cell({ k, v, mono, highlight }: { k: string; v: string; mono?: boolean; highlight?: 'warn' }) {
  return (
    <>
      <span className="text-ink-muted">{k}</span>
      <span className={clsx(mono && 'font-mono', highlight === 'warn' ? 'text-warn' : 'text-ink-secondary')}>{v}</span>
    </>
  );
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] uppercase tracking-wider text-ink-muted font-medium">{title}</p>
      {children}
    </div>
  );
}
function DepRow({ id, name, type, direction }: { id: string; name?: string; type?: string; direction: 'in' | 'out' }) {
  const typeLabel = type === 'benefits_from' ? 'soft' : type === 'requires_arrival_by' ? 'arrival_by' : 'requires';
  return (
    <div className="flex items-center gap-1.5 text-[12px]">
      <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', direction === 'in' ? 'bg-info' : 'bg-acid')} />
      <span className="text-ink-secondary font-mono truncate">{name ?? id}</span>
      <span className="text-ink-muted ml-auto flex-shrink-0">{typeLabel}</span>
    </div>
  );
}
function MiniStat({ label, value, unit, highlight }: { label: string; value: string; unit: string; highlight?: 'warn' | 'good' }) {
  return (
    <div className="bg-surface-800 rounded p-2 text-center">
      <p className="text-[11px] text-ink-muted uppercase">{label}</p>
      <p className={clsx('text-base font-bold font-mono', highlight === 'warn' ? 'text-warn' : highlight === 'good' ? 'text-ok' : 'text-ink-secondary')}>{value}</p>
      <p className="text-[11px] text-ink-muted">{unit}</p>
    </div>
  );
}

// ── Controls bar ──────────────────────────────────────────────────────────────

interface ControlsBarProps {
  hideDone: boolean;
  onToggleHideDone: () => void;
  filterState: string;
  onFilterChange: (s: string) => void;
  onFit: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  showMiniMap: boolean;
  onToggleMiniMap: () => void;
  hasSelection: boolean;
  onCenterSelected: () => void;
  entityCount: number;
  visibleCount: number;
}

function GraphControlsBar({
  hideDone, onToggleHideDone, filterState, onFilterChange,
  onFit, onZoomIn, onZoomOut, onReset,
  showMiniMap, onToggleMiniMap, hasSelection, onCenterSelected,
  entityCount, visibleCount,
}: ControlsBarProps) {
  const FILTER_OPTS = ['all', 'blocked', 'external', 'at_risk', 'healthy', 'done', 'in_progress', 'pending'];

  return (
    <div className="absolute top-2 left-2 right-2 z-10 flex items-center gap-2 pointer-events-none flex-wrap">
      {/* Left: zoom/fit controls */}
      <div className="flex items-center gap-1 bg-surface-850/90 border border-surface-700 rounded-lg p-1 pointer-events-auto shadow-lg backdrop-blur-sm">
        <CtrlBtn title="Fit to screen" onClick={onFit}><Maximize2 className="w-4 h-4" /></CtrlBtn>
        <CtrlBtn title="Zoom in"       onClick={onZoomIn}><ZoomIn className="w-4 h-4" /></CtrlBtn>
        <CtrlBtn title="Zoom out"      onClick={onZoomOut}><ZoomOut className="w-4 h-4" /></CtrlBtn>
        <div className="w-px h-4 bg-surface-700 mx-0.5" />
        <CtrlBtn title="Reset layout"  onClick={onReset}><RotateCcw className="w-4 h-4" /></CtrlBtn>
        {hasSelection && (
          <CtrlBtn title="Center on selected node" onClick={onCenterSelected}><Crosshair className="w-4 h-4" /></CtrlBtn>
        )}
        <div className="w-px h-4 bg-surface-700 mx-0.5" />
        <CtrlBtn
          title={showMiniMap ? 'Hide minimap' : 'Show minimap'}
          onClick={onToggleMiniMap}
        >
          {showMiniMap ? <Map className="w-4 h-4 text-acid" /> : <Map className="w-4 h-4" />}
        </CtrlBtn>
      </div>

      {/* Middle: hide-done + filter */}
      <div className="flex items-center gap-1.5 bg-surface-850/90 border border-surface-700 rounded-lg px-2 py-1.5 pointer-events-auto shadow-lg backdrop-blur-sm">
        <button
          title={hideDone ? 'Show completed' : 'Hide completed'}
          onClick={onToggleHideDone}
          className={clsx(
            'flex items-center gap-1 text-[11px] font-medium rounded px-1.5 py-0.5 transition-colors',
            hideDone ? 'text-acid bg-acid/10' : 'text-ink-muted hover:text-ink',
          )}
        >
          {hideDone ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          <span className="hidden sm:inline">Done</span>
        </button>

        <div className="w-px h-4 bg-surface-700" />

        <div className="flex items-center gap-1">
          <Filter className="w-3.5 h-3.5 text-ink-muted" />
          <select
            value={filterState}
            onChange={(e) => onFilterChange(e.target.value)}
            className="bg-transparent text-[11px] text-ink-secondary focus:outline-none cursor-pointer"
            title="Filter by state"
            aria-label="Filter nodes by state"
          >
            {FILTER_OPTS.map((opt) => (
              <option key={opt} value={opt} className="bg-surface-850">
                {opt === 'all' ? 'All states' : opt.replace('_', ' ')}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Right: entity count */}
      <div className="ml-auto bg-surface-850/90 border border-surface-700 rounded-lg px-2.5 py-1.5 pointer-events-none shadow-lg backdrop-blur-sm">
        <span className="text-[11px] text-ink-muted font-mono">
          {visibleCount}/{entityCount} nodes
        </span>
      </div>
    </div>
  );
}

function CtrlBtn({ title, onClick, children }: { title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="p-1.5 rounded text-ink-muted hover:text-ink hover:bg-surface-700 transition-colors"
    >
      {children}
    </button>
  );
}

// ── Inner graph (needs useReactFlow) ─────────────────────────────────────────

interface GraphInnerProps {
  scenario: Scenario;
  triageResult?: TriageResult | null;
  criticalPath: string[];
}

function GraphInner({ scenario, triageResult, criticalPath }: GraphInnerProps) {
  const [state] = useApp();
  const { highlightedEntityId, comparisonResult, selectedOptionId } = state;

  const rf = useReactFlow();
  const [selected, setSelected] = useState<{ entity: Entity; triageState?: TriageState } | null>(null);
  const [hideDone, setHideDone]     = useState(false);
  const [filterState, setFilterState] = useState('all');
  const [showMiniMap, setShowMiniMap] = useState(true);
  const [legendOpen, setLegendOpen]   = useState(true);
  // layoutKey: increment to re-trigger position computation
  const [layoutKey, setLayoutKey]   = useState(0);

  // Sync highlighted entity from store (clicked in ImpactPanel). This is a
  // deliberate navigation action, so we also bring the node into view once.
  useEffect(() => {
    if (!highlightedEntityId) return;
    const entity = scenario.entities.find((e) => e.id === highlightedEntityId);
    if (entity) {
      const triageState = triageResult?.states.find((s) => s.entity_id === highlightedEntityId)?.state;
      setSelected({ entity, triageState });
      void rf.fitView({ nodes: [{ id: highlightedEntityId }], padding: 0.6, maxZoom: 1.4, duration: 400 });
    }
  }, [highlightedEntityId, scenario.entities, triageResult, rf]);

  const stateMap = useMemo<Record<string, TriageState>>(() => {
    if (!triageResult) return {};
    return Object.fromEntries(triageResult.states.map((s) => [s.entity_id, s.state]));
  }, [triageResult]);

  const evidencePathIds = useMemo<Set<string>>(() => {
    if (!selected || !triageResult) return new Set();
    const entry = triageResult.states.find((s) => s.entity_id === selected.entity.id);
    return new Set(entry?.evidence_path ?? []);
  }, [selected, triageResult]);

  const critPathSet = useMemo(() => new Set(criticalPath), [criticalPath]);
  const disruptedId = triageResult?.disruption.entity_id;

  // Filtered entity list
  const visibleEntities = useMemo(() => {
    return scenario.entities.filter((e) => {
      const state = stateMap[e.id] ?? e.status;
      if (hideDone && (state === 'done' || e.status === 'done')) return false;
      if (filterState !== 'all' && state !== filterState) return false;
      return true;
    });
  }, [scenario.entities, stateMap, hideDone, filterState]);

  const visibleIds = useMemo(() => new Set(visibleEntities.map((e) => e.id)), [visibleEntities]);

  const positions = useMemo(
    () => computeLayout(visibleEntities, scenario.dependencies.filter(
      (d) => visibleIds.has(d.prerequisite) && visibleIds.has(d.dependent),
    )),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scenario, visibleEntities, visibleIds, layoutKey],
  );

  const handleSelect = useCallback((entity: Entity, triageState?: TriageState) => {
    setSelected((prev) => prev?.entity.id === entity.id ? null : { entity, triageState });
  }, []);

  // Forecast data for selected option (for detail panel)
  const recEntry = selectedOptionId
    ? comparisonResult?.ranked.find((r) => r.option_id === selectedOptionId)
    : comparisonResult?.ranked[0];
  const forecastForPanel = recEntry?.forecast ?? null;

  const nodes: Node[] = useMemo(
    () => visibleEntities.map((entity) => ({
      id: entity.id,
      type: 'entity',
      position: positions[entity.id] ?? { x: 0, y: 0 },
      data: {
        entity,
        triageState: stateMap[entity.id],
        isCriticalPath:   critPathSet.has(entity.id),
        isOnEvidencePath: evidencePathIds.has(entity.id),
        isDisrupted:      entity.id === disruptedId,
        isHighlighted:    entity.id === highlightedEntityId,
        onSelect: handleSelect,
      } satisfies NodeData,
      selected: false,
    })),
    [visibleEntities, positions, stateMap, critPathSet, evidencePathIds, disruptedId, highlightedEntityId, handleSelect],
  );

  const edges: Edge[] = useMemo(
    () => scenario.dependencies
      .filter((d) => visibleIds.has(d.prerequisite) && visibleIds.has(d.dependent))
      .map((dep, i) => {
        const isSoft     = dep.dependency_type === 'benefits_from';
        const isCritEdge = critPathSet.has(dep.prerequisite) && critPathSet.has(dep.dependent);
        const isEvidence = evidencePathIds.has(dep.prerequisite) && evidencePathIds.has(dep.dependent);

        return {
          id: `e-${i}-${dep.prerequisite}-${dep.dependent}`,
          source: dep.prerequisite,
          target: dep.dependent,
          animated: isEvidence,
          style: {
            stroke: isSoft ? '#737368' : isCritEdge ? '#FFBF4A' : isEvidence ? '#FF6464' : '#4A4A3C',
            strokeWidth: isCritEdge ? 2.5 : isEvidence ? 2.5 : 2,
            strokeDasharray: isSoft ? '5 3' : undefined,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: isSoft ? '#737368' : isCritEdge ? '#FFBF4A' : isEvidence ? '#FF6464' : '#3E3E32',
            width: 14,
            height: 14,
          },
          label: dep.dependency_type === 'requires_arrival_by' ? 'arrival_by' : undefined,
          labelStyle: { fill: '#A3A397', fontSize: 11, fontFamily: 'monospace' },
          labelBgStyle: { fill: '#0B0B09' },
        };
      }),
    [scenario.dependencies, visibleIds, critPathSet, evidencePathIds],
  );

  const selectedEntry = selected
    ? triageResult?.states.find((s) => s.entity_id === selected.entity.id)
    : undefined;

  // Fit-to-screen caps zoom at 1× so small graphs are never blown up, and
  // large graphs stay navigable instead of shrinking every node to dots.
  const fitToScreen = useCallback(
    () => rf.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }),
    [rf],
  );
  const centerSelected = useCallback(() => {
    if (!selected) return;
    void rf.fitView({ nodes: [{ id: selected.entity.id }], padding: 0.5, maxZoom: 1.4, duration: 300 });
  }, [rf, selected]);

  return (
    <div className="relative w-full h-full">
      <GraphControlsBar
        hideDone={hideDone}
        onToggleHideDone={() => setHideDone((v) => !v)}
        filterState={filterState}
        onFilterChange={setFilterState}
        onFit={fitToScreen}
        onZoomIn={() => rf.zoomIn({ duration: 200 })}
        onZoomOut={() => rf.zoomOut({ duration: 200 })}
        onReset={() => { setLayoutKey((k) => k + 1); setTimeout(() => rf.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 50); }}
        showMiniMap={showMiniMap}
        onToggleMiniMap={() => setShowMiniMap((v) => !v)}
        hasSelection={selected != null}
        onCenterSelected={centerSelected}
        entityCount={scenario.entities.length}
        visibleCount={visibleEntities.length}
      />

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        minZoom={0.2}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#303027" />
        <Controls showInteractive={false} className="hidden" />
        {showMiniMap && (
          <MiniMap
            nodeColor={(n) => {
              const s = (n.data as NodeData)?.triageState ?? (n.data as NodeData)?.entity?.status;
              const c: Record<string, string> = {
                blocked: '#FF6464', external: '#75BFFF', at_risk: '#FFBF4A',
                healthy: '#75E6A0', done: '#737368', pending: '#A3A397', in_progress: '#56A8E0',
              };
              return c[s] ?? '#3E3E32';
            }}
            maskColor="rgba(11, 11, 9, 0.75)"
            maskStrokeColor="#D9FF00"
            maskStrokeWidth={2}
            nodeStrokeWidth={0}
            zoomable
            pannable
            style={{ bottom: 16, right: 12, width: 168, height: 112 }}
            aria-label="Graph minimap"
          />
        )}

        {/* Legend — collapsible, bottom-left, out of the node area */}
        <div className="absolute bottom-4 left-3 z-10">
          <div className="card p-3 space-y-1.5 text-[11px] bg-surface-850/95 backdrop-blur-sm">
            <button
              className="flex items-center gap-1.5 text-ink-muted uppercase tracking-widest mb-1 hover:text-ink transition-colors w-full"
              onClick={() => setLegendOpen((v) => !v)}
              aria-expanded={legendOpen}
            >
              Legend
              {legendOpen ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
            </button>
            {legendOpen && (
              <>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                  {[
                    { state: 'blocked',     label: 'Blocked' },
                    { state: 'external',    label: 'External' },
                    { state: 'at_risk',     label: 'At Risk' },
                    { state: 'healthy',     label: 'Healthy' },
                    { state: 'done',        label: 'Done' },
                    { state: 'in_progress', label: 'In Progress' },
                    { state: 'pending',     label: 'Pending' },
                  ].map(({ state }) => (
                    <div key={state} className="flex items-center gap-1.5">
                      <StatusBadge status={state} showDot />
                    </div>
                  ))}
                </div>
                <div className="pt-1.5 border-t border-surface-700 space-y-1">
                  <div className="flex items-center gap-1.5">
                    <div className="w-6 h-0.5 bg-warn rounded-full" />
                    <span className="text-ink-muted">Critical path</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-6 h-px border-t-2 border-dashed border-ink-muted" />
                    <span className="text-ink-muted">benefits_from (soft)</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-6 h-0.5 bg-fail rounded-full" />
                    <span className="text-ink-muted">Evidence path</span>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </ReactFlow>

      {/* Node detail panel */}
      {selected && (
        <NodeDetailPanel
          entity={selected.entity}
          triageState={selected.triageState}
          triageEntry={selectedEntry}
          scenario={scenario}
          comparisonForecast={forecastForPanel}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

// ── Public component (wraps with ReactFlowProvider) ───────────────────────────

interface Props {
  scenario: Scenario;
  triageResult?: TriageResult | null;
  criticalPath?: string[];
}

export function DependencyGraph({ scenario, triageResult, criticalPath = [] }: Props) {
  return (
    <ReactFlowProvider>
      <GraphInner scenario={scenario} triageResult={triageResult} criticalPath={criticalPath} />
    </ReactFlowProvider>
  );
}
