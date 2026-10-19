/**
 * ActivityView — full-page view of the session activity log.
 * Shows workflow stages (in order) plus lifecycle events (MCP connect,
 * tool discovery, assistant turns) exactly as they were dispatched.
 */
import { Activity, CheckCircle2, Circle, Loader2, XCircle, Clock } from 'lucide-react';
import { useAppState, WORKFLOW_STAGES, STAGE_LABELS, type ActivityStatus } from '../store/appStore';
import { EmptyState } from './ui/EmptyState';
import { clsx } from 'clsx';

const STATUS_ICON: Record<ActivityStatus, React.ReactNode> = {
  done:     <CheckCircle2 className="w-4 h-4 text-ok" />,
  running:  <Loader2 className="w-4 h-4 text-info animate-spin" />,
  failed:   <XCircle className="w-4 h-4 text-fail" />,
  awaiting: <Clock className="w-4 h-4 text-warn" />,
  pending:  <Circle className="w-4 h-4 text-border" />,
};

const STATUS_TEXT: Record<ActivityStatus, string> = {
  done: 'text-ok',
  running: 'text-info',
  failed: 'text-fail',
  awaiting: 'text-warn',
  pending: 'text-ink-muted',
};

export function ActivityView() {
  const { activityLog } = useAppState();

  const stageMap = new Map(activityLog.map((e) => [e.stage, e]));
  const extraEvents = activityLog.filter((e) => !WORKFLOW_STAGES.includes(e.stage as never));

  const rows = WORKFLOW_STAGES.map((stage) => {
    const event = stageMap.get(stage);
    return {
      id: stage,
      label: STAGE_LABELS[stage],
      status: (event?.status ?? 'pending') as ActivityStatus,
      detail: event?.detail,
      ts: event?.ts,
    };
  });

  if (activityLog.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No activity yet"
        description="Workflow events appear here once you connect to the backend or run a simulation."
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Workflow stages */}
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-acid" />
            <span className="text-sm font-semibold text-ink">Workflow Stages</span>
          </div>
          <span className="text-[10px] text-ink-muted font-mono">{activityLog.length} events</span>
        </div>
        <ol className="divide-y divide-border">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center gap-3 px-4 py-3">
              <span className="flex-shrink-0">{STATUS_ICON[row.status]}</span>
              <span className={clsx('text-sm font-medium', STATUS_TEXT[row.status])}>{row.label}</span>
              {row.detail && (
                <span className="text-xs text-ink-muted font-mono truncate ml-2" title={row.detail}>
                  {row.detail}
                </span>
              )}
              {row.ts && (
                <span className="ml-auto text-[10px] text-ink-muted font-mono flex-shrink-0">
                  {new Date(row.ts).toLocaleTimeString()}
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>

      {/* Lifecycle events */}
      {extraEvents.length > 0 && (
        <div className="card">
          <div className="card-header">
            <span className="text-sm font-semibold text-ink">Client & Assistant Events</span>
          </div>
          <ul className="divide-y divide-border">
            {extraEvents.map((event) => (
              <li key={event.id} className="flex items-center gap-3 px-4 py-2.5">
                <span className="flex-shrink-0">{STATUS_ICON[event.status]}</span>
                <span className={clsx('text-xs font-medium', STATUS_TEXT[event.status])}>{event.label}</span>
                {event.detail && (
                  <span className="text-xs text-ink-muted font-mono truncate" title={event.detail}>
                    {event.detail}
                  </span>
                )}
                <span className="ml-auto text-[10px] text-ink-muted font-mono flex-shrink-0">
                  {new Date(event.ts).toLocaleTimeString()}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
