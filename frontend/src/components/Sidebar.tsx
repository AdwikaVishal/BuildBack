/**
 * Sidebar — compact navigation for the control center.
 *
 * Desktop: fixed rail, collapsible to an icon strip (≈64px).
 * Mobile:   off-canvas drawer toggled from the top header.
 * Active destination is marked with the lime accent; connection state at the
 * bottom comes from real MCP status, never a simulated "connected".
 */
import { clsx } from 'clsx';
import {
  LayoutDashboard,
  GitBranch,
  Zap,
  AlertTriangle,
  LifeBuoy,
  CalendarRange,
  ClipboardCheck,
  Activity,
  Terminal,
  ChevronsLeft,
  ChevronsRight,
  X,
} from 'lucide-react';
import type { DashboardView } from '../types';
import { useApp } from '../store/appStore';

interface NavItem {
  view: DashboardView;
  label: string;
  icon: React.ElementType;
}

const NAV: NavItem[] = [
  { view: 'overview',    label: 'Overview',          icon: LayoutDashboard },
  { view: 'graph',       label: 'Dependency Graph',  icon: GitBranch },
  { view: 'disruptions', label: 'Disruptions',       icon: Zap },
  { view: 'impact',      label: 'Impact Analysis',   icon: AlertTriangle },
  { view: 'recovery',    label: 'Recovery Planner',  icon: LifeBuoy },
  { view: 'timeline',    label: 'Schedule',          icon: CalendarRange },
  { view: 'review',      label: 'Review Plan',       icon: ClipboardCheck },
  { view: 'activity',    label: 'Activity Log',      icon: Activity },
  { view: 'mcp',         label: 'MCP Inspector',     icon: Terminal },
];

interface Props {
  /** Mobile drawer open state (drawer only; ignored on desktop). */
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
}

export function Sidebar({ mobileOpen, onCloseMobile }: Props) {
  const [state, dispatch] = useApp();
  const { activeView, triageResult, comparisonResult, mcpStatus, sidebarCollapsed } = state;
  const collapsed = sidebarCollapsed;

  const nav = (
    <div
      className={clsx(
        'h-full flex flex-col bg-surface-900 border-r border-surface-700 transition-[width] duration-200',
        collapsed ? 'w-[64px]' : 'w-[212px]',
      )}
    >
      {/* Brand */}
      <div className={clsx('flex items-center border-b border-surface-700', collapsed ? 'flex-col gap-2 px-2 py-3' : 'gap-2.5 px-4 py-4')}>
        <div className="w-8 h-8 rounded-lg bg-acid flex items-center justify-center flex-shrink-0">
          {/* Ripple mark: concentric arcs = disruption propagating outward */}
          <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="#0B0B09" strokeWidth="2.2" strokeLinecap="round">
            <circle cx="12" cy="12" r="1.6" fill="#0B0B09" stroke="none" />
            <path d="M8.5 12a3.5 3.5 0 0 1 3.5-3.5" />
            <path d="M15.5 12a3.5 3.5 0 0 1-3.5 3.5" opacity="0.55" />
            <path d="M5.5 12a6.5 6.5 0 0 1 6.5-6.5" opacity="0.35" />
          </svg>
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <p className="text-sm font-bold text-ink leading-tight font-display tracking-tight">RIPPLEBACK</p>
            <p className="text-[9px] text-ink-muted tracking-[0.16em] uppercase leading-tight">Recovery Control</p>
          </div>
        )}
        {/* Desktop collapse toggle */}
        <button
          className={clsx('hidden lg:flex p-1 rounded text-ink-muted hover:text-ink hover:bg-surface-800 transition-colors', collapsed ? '' : 'ml-auto')}
          onClick={() => dispatch({ type: 'SET_SIDEBAR_COLLAPSED', collapsed: !collapsed })}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronsRight className="w-4 h-4" /> : <ChevronsLeft className="w-4 h-4" />}
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto" aria-label="Primary">
        {NAV.map(({ view, label, icon: Icon }) => {
          const isActive = activeView === view;
          const hasBadge =
            (view === 'impact' && triageResult != null) ||
            (view === 'recovery' && (comparisonResult?.ranked.length ?? 0) > 0);

          return (
            <button
              key={view}
              className={clsx(
                'relative w-full flex items-center gap-3 rounded-lg text-left transition-colors text-sm font-medium',
                collapsed ? 'justify-center px-2 py-2.5' : 'px-3 py-2.5',
                isActive
                  ? 'bg-acid/10 text-acid'
                  : 'text-ink-secondary hover:text-ink hover:bg-surface-800/60',
              )}
              onClick={() => {
                dispatch({ type: 'SET_VIEW', view });
                onCloseMobile?.();
              }}
              title={collapsed ? label : undefined}
              aria-current={isActive ? 'page' : undefined}
            >
              {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-full bg-acid" aria-hidden="true" />}
              <div className="relative flex-shrink-0">
                <Icon className="w-[18px] h-[18px]" />
                {hasBadge && (
                  <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-acid" />
                )}
              </div>
              {!collapsed && <span className="truncate">{label}</span>}
            </button>
          );
        })}
      </nav>

      {/* Connection / environment */}
      <div className={clsx('border-t border-surface-700', collapsed ? 'px-2 py-3 flex flex-col items-center gap-2' : 'px-4 py-3 space-y-1.5')}>
        <div className="flex items-center gap-2" title={mcpStatus.connected ? `Connected to ${mcpStatus.serverName ?? 'RippleBack'} ${mcpStatus.serverVersion ? `v${mcpStatus.serverVersion}` : ''}` : (mcpStatus.error ?? 'MCP server unreachable')}>
          <span
            className={clsx('w-2 h-2 rounded-full flex-shrink-0', mcpStatus.connected ? 'bg-ok' : 'bg-fail')}
            aria-hidden="true"
          />
          {!collapsed && (
            <span className={clsx('text-[11px] font-medium', mcpStatus.connected ? 'text-ok' : 'text-fail')}>
              {mcpStatus.connected ? 'MCP connected' : 'MCP offline'}
            </span>
          )}
        </div>
        {!collapsed && (
          <p className="text-[10px] text-ink-muted font-mono truncate">
            {mcpStatus.connected
              ? `${mcpStatus.tools?.length ?? 0} tools · read-only`
              : 'backend unreachable'}
          </p>
        )}
        {!collapsed && (
          <p className="text-[10px] text-ink-muted/70 leading-tight">
            Hypothetical analysis only — nothing is committed.
          </p>
        )}
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop */}
      <div className="hidden lg:flex flex-shrink-0 relative">{nav}</div>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="lg:hidden fixed inset-0 z-50 flex">
          <div className="relative animate-panel-in">{nav}</div>
          <button
            className="flex-1 bg-surface-950/70 backdrop-blur-sm"
            onClick={onCloseMobile}
            aria-label="Close navigation"
          />
          <button
            className="absolute top-3 left-[72px] p-1.5 rounded-lg bg-surface-850 border border-surface-700 text-ink-secondary"
            onClick={onCloseMobile}
            aria-label="Close navigation"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
    </>
  );
}
