/**
 * ActivityLog — compact workflow-stage tracker.
 * Shows which stages of the disruption → recovery workflow are complete,
 * running, pending, or failed. All entries come from real dispatched events.
 */
import { clsx } from 'clsx';
import { CheckCircle2, Circle, Loader2, XCircle, Clock, ChevronRight } from 'lucide-react';
import { useAppState, WORKFLOW_STAGES, STAGE_LABELS, type ActivityStatus } from '../store/appStore';

const STATUS_ICON: Record<ActivityStatus, React.ReactNode> = {
  done:     <CheckCircle2 className="w-4 h-4 text-ok flex-shrink-0" />,
  running:  <Loader2      className="w-4 h-4 text-info animate-spin flex-shrink-0" />,
  failed:   <XCircle      className="w-4 h-4 text-fail flex-shrink-0" />,
  awaiting: <Clock        className="w-4 h-4 text-warn flex-shrink-0" />,
  pending:  <Circle       className="w-4 h-4 text-border flex-shrink-0" />,
};

const STATUS_TEXT: Record<ActivityStatus, string> = {
  done:     'text-ok',
  running:  'text-info',
  failed:   'text-fail',
  awaiting: 'text-warn',
  pending:  'text-ink-muted',
};

export function ActivityLog() {
  const { activityLog } = useAppState();

  // Build a merged view: workflow stages first (in order), then any extra
  // events (MCP connect/disconnect, tool discovery, assistant turns).
  const stageMap = new Map(activityLog.map((e) => [e.stage, e]));

  const EXTRA_LABELS: Record<string, string> = {
    mcp_connected: 'MCP client',
    tool_discovery: 'Tool discovery',
    assistant_turn: 'Assistant',
  };

  const extraEvents = activityLog.filter((e) => !WORKFLOW_STAGES.includes(e.stage as never));

  // Compact labels for the horizontal strip — full labels stay in tooltips.
  const SHORT_LABELS: Record<string, string> = {
    disruption_detected:  'Disruption',
    simulation_completed: 'Simulated',
    impact_analyzed:      'Impact',
    recovery_generated:   'Recovery',
    option_selected:      'Option',
    schedule_generated:   'Schedule',
  };

  const rows = WORKFLOW_STAGES.map((stage) => {
    const event = stageMap.get(stage);
    return {
      stage,
      label: STAGE_LABELS[stage],
      short: SHORT_LABELS[stage] ?? STAGE_LABELS[stage],
      status: (event?.status ?? 'pending') as ActivityStatus,
      detail: event?.detail,
    };
  });

  if (activityLog.length === 0) return null;

  const statusBg: Record<ActivityStatus, string> = {
  done:     'bg-ok/15',
  running:  'bg-info/15',
  failed:   'bg-fail/15',
  awaiting: 'bg-warn/15',
  pending:  'bg-surface-800',
};

  return (
    <div className="border-b border-border bg-surface-900/60 px-4 py-1.5 flex items-center gap-1 overflow-x-auto flex-shrink-0">
      <span className="text-[11px] uppercase tracking-widest text-ink-muted mr-2 flex-shrink-0">Workflow</span>
      {rows.map((row, i) => (
        <div key={row.stage} className="flex items-center gap-1 flex-shrink-0">
          {/* Step */}
          <div
            className={clsx(
              'flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium transition-colors',
              statusBg[row.status],
              row.status === 'pending' && 'opacity-40',
            )}
            title={`${row.label}${row.detail ? ` — ${row.detail}` : ''}`}
          >
            {STATUS_ICON[row.status]}
            <span className={clsx('hidden sm:inline', STATUS_TEXT[row.status])}>
              {row.short}
            </span>
          </div>
          {/* Connector arrow */}
          {i < rows.length - 1 && (
            <ChevronRight className="w-3 h-3 text-border flex-shrink-0" />
          )}
        </div>
      ))}

      {/* Client / assistant lifecycle events */}
      {extraEvents.length > 0 && (
        <>
          <span className="text-ink-muted mx-1 flex-shrink-0">|</span>
          {extraEvents.map((event) => (
            <div
              key={event.id}
              className={clsx(
                'flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium flex-shrink-0',
                statusBg[event.status],
                event.status === 'pending' && 'opacity-40',
              )}
              title={event.detail ? `${event.label} — ${event.detail}` : event.label}
            >
              {STATUS_ICON[event.status]}
              <span className={clsx('hidden sm:inline', STATUS_TEXT[event.status])}>
                {EXTRA_LABELS[event.stage] ?? event.label}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
