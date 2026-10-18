import { clsx } from 'clsx';
import {
  GitBranch,
  AlertTriangle,
  LifeBuoy,
  BarChart2,
  ShieldCheck,
  Terminal,
  Zap,
} from 'lucide-react';
import type { DashboardView } from '../types';
import { useApp } from '../store/appStore';

interface NavItem {
  view: DashboardView;
  label: string;
  icon: React.ElementType;
  badge?: string;
}

const NAV: NavItem[] = [
  { view: 'graph',    label: 'Dependency Graph', icon: GitBranch },
  { view: 'impact',   label: 'Impact Analysis',  icon: AlertTriangle },
  { view: 'recovery', label: 'Recovery Options', icon: LifeBuoy },
  { view: 'timeline', label: 'Timeline',         icon: BarChart2 },
  { view: 'approval', label: 'Approval',         icon: ShieldCheck },
  { view: 'mcp',      label: 'MCP Inspector',    icon: Terminal },
];

export function Sidebar() {
  const [state, dispatch] = useApp();
  const { activeView, triageResult, comparisonResult, mcpStatus } = state;

  return (
    <aside className="w-14 lg:w-52 flex-shrink-0 bg-surface-800 border-r border-surface-600 flex flex-col">
      {/* Logo / brand */}
      <div className="px-3 py-4 border-b border-surface-600 flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-accent-blue to-accent-purple flex items-center justify-center flex-shrink-0">
          <Zap className="w-4 h-4 text-white" />
        </div>
        <div className="hidden lg:block">
          <p className="text-sm font-bold text-white leading-tight">RippleBack</p>
          <p className="text-[10px] text-gray-600">Disruption Intelligence</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 space-y-0.5 px-1.5">
        {NAV.map(({ view, label, icon: Icon }) => {
          const isActive = activeView === view;
          // Badge dots for panels that have data
          const hasBadge =
            (view === 'impact' && triageResult != null) ||
            (view === 'recovery' && comparisonResult != null && comparisonResult.ranked.length > 0);

          return (
            <button
              key={view}
              className={clsx(
                'w-full flex items-center gap-3 px-2.5 py-2 rounded-md text-left transition-colors text-sm',
                isActive
                  ? 'bg-surface-600 text-white'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-surface-700',
              )}
              onClick={() => dispatch({ type: 'SET_VIEW', view })}
              title={label}
            >
              <div className="relative flex-shrink-0">
                <Icon className="w-4 h-4" />
                {hasBadge && (
                  <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-accent-blue" />
                )}
              </div>
              <span className="hidden lg:inline truncate">{label}</span>
            </button>
          );
        })}
      </nav>

      {/* MCP status dot */}
      <div className="px-3 py-3 border-t border-surface-600 flex items-center gap-2">
        <div className={clsx(
          'w-2 h-2 rounded-full flex-shrink-0',
          mcpStatus.connected ? 'bg-emerald-500 shadow-[0_0_6px_#10b981]' : 'bg-red-700',
        )} />
        <span className="hidden lg:inline text-[10px] text-gray-600 truncate">
          {mcpStatus.connected ? 'MCP connected' : 'MCP offline (mock)'}
        </span>
      </div>
    </aside>
  );
}
