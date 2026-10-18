/**
 * ActivityLog — compact workflow-stage tracker.
 * Shows which stages of the disruption → recovery workflow are complete,
 * running, pending, or failed.
 */
import { clsx } from 'clsx';
import { CheckCircle2, Circle, Loader2, XCircle, Clock, ChevronRight } from 'lucide-react';
import { useAppState, WORKFLOW_STAGES, STAGE_LABELS, type ActivityStatus } from '../store/appStore';

const STATUS_ICON: Record<ActivityStatus, React.ReactNode> = {
  done:     <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />,
  running:  <Loader2      className="w-3.5 h-3.5 text-blue-400 animate-spin flex-shrink-0" />,
  failed:   <XCircle      className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />,
  awaiting: <Clock        className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />,
  pending:  <Circle       className="w-3.5 h-3.5 text-gray-700 flex-shrink-0" />,
};

const STATUS_TEXT: Record<ActivityStatus, string> = {
  done:     'text-emerald-300',
  running:  'text-blue-300',
  failed:   'text-red-400',
  awaiting: 'text-amber-300',
  pending:  'text-gray-600',
};

export function ActivityLog() {
  const { activityLog } = useAppState();

  // Build a merged view: workflow stages first (in order), then any extra events
  const stageMap = new Map(activityLog.map((e) => [e.stage, e]));

  const rows = WORKFLOW_STAGES.map((stage) => {
    const event = stageMap.get(stage);
    return {
      stage,
      label: STAGE_LABELS[stage],
      status: (event?.status ?? 'pending') as ActivityStatus,
      detail: event?.detail,
    };
  });

  if (activityLog.length === 0) return null;

  return (
    <div className="border-b border-surface-600 bg-surface-900/60 px-4 py-2 flex items-center gap-1 overflow-x-auto flex-shrink-0">
      <span className="text-[9px] uppercase tracking-widest text-gray-700 mr-2 flex-shrink-0">Workflow</span>
      {rows.map((row, i) => (
        <div key={row.stage} className="flex items-center gap-1 flex-shrink-0">
          {/* Step */}
          <div
            className={clsx(
              'flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-medium transition-colors',
              row.status === 'done'     && 'bg-emerald-900/30',
              row.status === 'running'  && 'bg-blue-900/30',
              row.status === 'failed'   && 'bg-red-900/30',
              row.status === 'awaiting' && 'bg-amber-900/30',
              row.status === 'pending'  && 'opacity-40',
            )}
            title={row.detail ?? row.label}
          >
            {STATUS_ICON[row.status]}
            <span className={clsx('hidden md:inline', STATUS_TEXT[row.status])}>
              {row.label}
            </span>
          </div>
          {/* Connector arrow */}
          {i < rows.length - 1 && (
            <ChevronRight className="w-3 h-3 text-gray-800 flex-shrink-0" />
          )}
        </div>
      ))}
    </div>
  );
}
