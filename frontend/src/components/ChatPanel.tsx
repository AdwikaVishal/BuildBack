/**
 * ChatPanel — conversational assistant (Person 4).
 *
 * Sends the user's message plus the currently selected scenario to the local
 * API adapter, which runs a genuine MCP session against the RippleBack server
 * and returns the reply together with the full tool-call trace. This panel
 * renders:
 *
 *  - user and assistant messages (light Markdown subset, no extra deps),
 *  - each invoked MCP tool with status, duration, and expandable arguments /
 *    structured results,
 *  - loading and error states, with Retry for recoverable failures,
 *  - connection status and the active scenario,
 *  - the interpretation provider actually used (deterministic vs Bedrock),
 *  - an explicit "hypothetical / read-only" reminder.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import {
  Send, Wifi, WifiOff, ChevronDown, ChevronUp, Clock, Loader2,
  RotateCcw, Bot, User, Wrench, AlertTriangle,
} from 'lucide-react';
import { useApp, useAppDispatch } from '../store/appStore';
import { sendChatMessage, AssistantError } from '../api/assistantClient';
import { scenarioLabel as scenarioDisplayLabel } from '../data/scenarios';
import type { ChatMessage, ChatStep } from '../types';
import { clsx } from 'clsx';

// ── Tiny Markdown renderer (bold, inline code, bullets, headings) ────────────

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('**')) {
      parts.push(<strong key={`${keyPrefix}-b${i}`} className="text-ink font-semibold">{token.slice(2, -2)}</strong>);
    } else {
      parts.push(<code key={`${keyPrefix}-c${i}`} className="font-mono text-[11px] bg-surface-700 rounded px-1 py-0.5 text-cyan-300">{token.slice(1, -1)}</code>);
    }
    last = match.index + token.length;
    i += 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function Markdown({ text }: { text: string }) {
  const lines = text.split('\n');
  const blocks: React.ReactNode[] = [];
  let bullets: string[] = [];
  const key = (n: number) => `md-${n}`;

  const flushBullets = (n: number) => {
    if (bullets.length === 0) return;
    blocks.push(
      <ul key={key(n)} className="list-disc list-inside space-y-0.5 my-1">
        {bullets.map((b, bi) => (
          <li key={bi} className="text-ink-secondary">{renderInline(b, `${n}-${bi}`)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    if (line.startsWith('- ')) {
      bullets.push(line.slice(2));
      return;
    }
    flushBullets(idx);
    if (line.startsWith('### ')) {
      blocks.push(<h4 key={key(idx)} className="text-ink font-semibold text-xs mt-2">{line.slice(4)}</h4>);
    } else if (line.startsWith('_') && line.endsWith('_') && line.length > 2) {
      blocks.push(<p key={key(idx)} className="text-ink-muted text-[11px] italic my-1">{renderInline(line.slice(1, -1), key(idx))}</p>);
    } else if (line.trim() === '') {
      blocks.push(<div key={key(idx)} className="h-1.5" />);
    } else {
      blocks.push(<p key={key(idx)} className="text-ink-secondary leading-relaxed">{renderInline(line, key(idx))}</p>);
    }
  });
  flushBullets(9999);
  return <div className="text-[13px] space-y-0.5">{blocks}</div>;
}

// ── Tool step card ───────────────────────────────────────────────────────────

function StepCard({ step }: { step: ChatStep }) {
  const [open, setOpen] = useState(false);
  const statusStyle =
    step.status === 'ok' ? 'border-ok/30 bg-ok/10'
    : step.status === 'error' ? 'border-fail/30 bg-red-900/10'
    : 'border-border bg-surface-800/60';
  const statusDot =
    step.status === 'ok' ? 'bg-ok'
    : step.status === 'error' ? 'bg-fail'
    : 'bg-ink-muted';

  return (
    <div className={clsx('rounded-md border text-[11px]', statusStyle)}>
      <button
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left"
        onClick={() => setOpen(!open)}
      >
        <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', statusDot)} />
        <Wrench className="w-3 h-3 text-ink-muted flex-shrink-0" />
        <span className="font-mono text-ink-secondary truncate">{step.tool}</span>
        {step.summary && !open && (
          <span className="text-ink-muted truncate hidden sm:inline" title={step.summary}>{step.summary}</span>
        )}
        {step.duration_ms != null && (
          <span className="flex items-center gap-1 text-ink-muted flex-shrink-0">
            <Clock className="w-2.5 h-2.5" />
            {step.duration_ms}ms
          </span>
        )}
        <span className={clsx(
          'ml-auto flex-shrink-0 text-[11px] px-1.5 rounded',
          step.status === 'ok' ? 'text-ok bg-ok/15'
          : step.status === 'error' ? 'text-fail bg-fail/15'
          : 'text-ink-muted bg-surface-700',
        )}>
          {step.status}
        </span>
        {open ? <ChevronUp className="w-3 h-3 text-ink-muted" /> : <ChevronDown className="w-3 h-3 text-ink-muted" />}
      </button>
      {open && (
        <div className="px-2.5 pb-2 space-y-1.5 border-t border-border/60 pt-1.5">
          <p className="text-[11px] uppercase tracking-wide text-ink-muted">Arguments</p>
          <pre className="bg-surface-900 border border-border rounded p-1.5 text-[11px] text-ink-secondary overflow-x-auto max-h-32">
            {JSON.stringify(step.arguments, null, 2)}
          </pre>
          {step.error && <p className="font-mono text-fail text-[11px]">{step.error}</p>}
          {step.result !== undefined && step.result !== null && (
            <>
              <p className="text-[11px] uppercase tracking-wide text-ink-muted">Structured result</p>
              <pre className="bg-surface-900 border border-border rounded p-1.5 text-[11px] text-ink-secondary overflow-x-auto max-h-56">
                {JSON.stringify(step.result, null, 2)}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Message bubble ───────────────────────────────────────────────────────────

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={clsx('flex gap-2.5', isUser && 'flex-row-reverse')}>
      <div className={clsx(
        'w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5',
        isUser ? 'bg-surface-600' : 'bg-gradient-to-br from-acid to-acid',
      )}>
        {isUser ? <User className="w-3 h-3 text-ink-secondary" /> : <Bot className="w-3 h-3 text-white" />}
      </div>
      <div className={clsx('max-w-[85%] space-y-1.5', isUser && 'text-right')}>
        <div className={clsx(
          'rounded-lg px-3 py-2 text-left',
          isUser
            ? 'bg-surface-700 border border-border'
            : message.error
              ? 'bg-fail/10 border border-fail/40'
              : 'bg-surface-800 border border-border',
        )}>
          {isUser ? (
            <p className="text-[13px] text-ink whitespace-pre-wrap">{message.text}</p>
          ) : (
            <>
              <Markdown text={message.text} />
              {message.pending && (
                <div className="flex items-center gap-1.5 text-ink-muted text-[11px] mt-1">
                  <Loader2 className="w-3 h-3 animate-spin" /> calling MCP tools…
                </div>
              )}
            </>
          )}
        </div>

        {/* Truncation honesty — never imply the full result was shown */}
        {!isUser && message.truncated && (
          <p className="text-[11px] text-warn flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3 flex-shrink-0" />
            The backend truncated parts of the tool results in this reply — expand a step below to inspect the raw result.
          </p>
        )}
        {!isUser && message.needsClarification && (
          <p className="text-[11px] text-info">
            The assistant asked for clarification — reply to narrow the request.
          </p>
        )}

        {/* Tool trace (assistant only) */}
        {!isUser && message.steps && message.steps.length > 0 && (
          <div className="space-y-1">
            {message.steps.map((step, i) => (
              <StepCard key={i} step={step} />
            ))}
          </div>
        )}

        {/* Provider + intent metadata */}
        {!isUser && !message.pending && message.provider && (
          <div className="flex items-center gap-2 text-[11px] text-ink-muted flex-wrap">
            <span className="font-mono">intent: {message.intent}</span>
            <span className={clsx(
              'px-1.5 py-0.5 rounded',
              message.provider === 'bedrock' ? 'bg-info/15 text-info' : 'bg-surface-700 text-ink-secondary',
            )}>
              provider: {message.provider}
            </span>
            {(message.providerNotes ?? []).map((note, i) => (
              <span key={i} className="text-warn/80 truncate max-w-[280px]" title={note}>{note}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Suggested prompts ────────────────────────────────────────────────────────

const SUGGESTIONS = [
  'Show me the current plan.',
  'What happens if the payment API goes down?',
  'What is blocked and what can continue?',
  'The payment API is down — what is blocked, find alternatives and compare the options.',
  'Can you rebalance the workload?',
  'Generate a recovery schedule after this disruption.',
];

// ── Main component ───────────────────────────────────────────────────────────

export function ChatPanel() {
  const [state] = useApp();
  const dispatch = useAppDispatch();
  const { chat, activeScenarioId, scenarios, mcpStatus } = state;
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastUserMessage = useRef<string | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages.length, chat.busy]);

  const submit = useCallback(async (text: string) => {
    const message = text.trim();
    if (!message || chat.busy) return;
    lastUserMessage.current = message;
    setInput('');

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`, role: 'user', text: message, ts: Date.now(),
    };
    const pendingId = `a-${Date.now() + 1}`;
    dispatch({ type: 'CHAT_ADD_MESSAGE', message: userMsg });
    dispatch({ type: 'CHAT_ADD_MESSAGE', message: {
      id: pendingId, role: 'assistant', text: '', ts: Date.now(), pending: true,
    }});
    dispatch({ type: 'CHAT_SET_BUSY', busy: true });

    try {
      const response = await sendChatMessage(message, activeScenarioId);
      dispatch({ type: 'CHAT_UPDATE_MESSAGE', id: pendingId, patch: {
        text: response.reply,
        pending: false,
        intent: response.intent,
        provider: response.provider,
        providerNotes: response.provider_notes,
        steps: response.steps,
        needsClarification: response.needs_clarification,
        truncated: response.truncated,
      }});
      dispatch({ type: 'LOG_ACTIVITY', stage: 'assistant_turn', label: 'Assistant turn completed', status: 'done', detail: `${response.intent} · ${response.tool_calls_made} MCP call(s)` });
    } catch (err) {
      const msg = err instanceof AssistantError ? err.message : String(err);
      const retryable = err instanceof AssistantError ? err.retryable : true;
      dispatch({ type: 'CHAT_UPDATE_MESSAGE', id: pendingId, patch: {
        text: retryable
          ? `${msg}\n\n_You can press Retry to send this again._`
          : msg,
        pending: false,
        error: true,
      }});
    } finally {
      dispatch({ type: 'CHAT_SET_BUSY', busy: false });
    }
  }, [activeScenarioId, chat.busy, dispatch]);

  const retryLast = useCallback(() => {
    if (lastUserMessage.current) void submit(lastUserMessage.current);
  }, [submit]);

  const lastMessage = chat.messages[chat.messages.length - 1];
  const lastFailed = !chat.busy && lastMessage?.role === 'assistant' && lastMessage.error;

  const scenarioLabel = scenarioDisplayLabel(
    activeScenarioId,
    scenarios.find((s) => s.scenario_id === activeScenarioId),
  );

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header: connection + scenario + provider */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-surface-800/60 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5 text-xs">
          {mcpStatus.connected
            ? <><Wifi className="w-3.5 h-3.5 text-ok" /><span className="text-ok">MCP connected</span></>
            : <><WifiOff className="w-3.5 h-3.5 text-fail" /><span className="text-fail">MCP offline</span></>
          }
        </div>
        <span className="text-ink-muted">·</span>
        <span className="text-xs text-ink-muted">
          analyzing <span className="font-mono text-ink-secondary">{activeScenarioId}</span> ({scenarioLabel})
        </span>
        {!mcpStatus.connected && (
          <span className="flex items-center gap-1 text-[11px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-0.5">
            <AlertTriangle className="w-3 h-3" />
            offline — tool calls will fail until the server is reachable
          </span>
        )}
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4 min-h-0">
        {chat.messages.length === 0 && (
          <div className="max-w-xl mx-auto text-center space-y-4 pt-8">
            <Bot className="w-10 h-10 text-ink-muted mx-auto" />
            <p className="text-sm text-ink-secondary">
              Ask the RippleBack assistant about your plan. It invokes the real
              MCP tools and shows you exactly what ran.
            </p>
            <div className="flex flex-wrap gap-1.5 justify-center">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => void submit(s)}
                  className="text-[11px] bg-surface-800 border border-border hover:border-acid/60 rounded-full px-3 py-1.5 text-ink-secondary hover:text-ink transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-ink-muted">
              All analysis is hypothetical and read-only — the committed plan is never modified.
            </p>
          </div>
        )}
        {chat.messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
      </div>

      {/* Input */}
      <div className="flex-shrink-0 border-t border-border p-3 bg-surface-900/60">
        {lastFailed && (
          <button
            onClick={retryLast}
            className="mb-2 flex items-center gap-1.5 text-[11px] text-warn bg-warn/10 border border-warn/30 rounded px-2.5 py-1 hover:bg-warn/20"
          >
            <RotateCcw className="w-3 h-3" /> Retry last message
          </button>
        )}
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); void submit(input); }}
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={mcpStatus.connected
              ? 'Ask about the plan, a disruption, or recovery options…'
              : 'MCP server offline — messages will fail until it is reachable'}
            className="flex-1 bg-surface-800 border border-border rounded-md px-3 py-2 text-sm text-ink placeholder-ink-muted focus:outline-none focus:border-acid"
          />
          <button
            type="submit"
            disabled={chat.busy || !input.trim()}
            className="btn-ghost text-xs px-3 py-2 disabled:opacity-40"
          >
            {chat.busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </form>
      </div>
    </div>
  );
}
