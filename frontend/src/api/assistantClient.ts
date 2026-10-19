// ─────────────────────────────────────────────────────────────────────────────
// Assistant client — talks to the local API adapter's /api/assistant/chat.
//
// The adapter (Python, backend) owns the genuine MCP session; the browser only
// sends the user's message plus the currently selected scenario. The reply
// carries the full tool-call trace (tool, arguments, status, duration, result)
// so the chat panel can render exactly which MCP tools ran.
// ─────────────────────────────────────────────────────────────────────────────

import type { ChatResponse, ScenarioId } from '../types';
import { MOCK_MODE, mockChatResponse } from '../data/mock';

export class AssistantError extends Error {
  retryable: boolean;
  constructor(message: string, retryable: boolean) {
    super(message);
    this.name = 'AssistantError';
    this.retryable = retryable;
  }
}

/**
 * Send one chat message to the assistant.
 *
 * Throws AssistantError on failure; `retryable` marks connection-type errors
 * so the UI can offer a Retry button.
 */
export async function sendChatMessage(
  message: string,
  scenarioId: ScenarioId | null,
): Promise<ChatResponse> {
  if (MOCK_MODE) {
    // Serve a captured-style reply without touching the backend.
    return mockChatResponse(message);
  }
  let resp: Response;
  try {
    resp = await fetch('/api/assistant/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        ...(scenarioId ? { scenario_id: scenarioId } : {}),
      }),
      signal: AbortSignal.timeout(120_000),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new AssistantError(`Assistant unreachable: ${msg}`, true);
  }

  let body: ChatResponse & { error?: string };
  try {
    body = await resp.json();
  } catch {
    throw new AssistantError(`Non-JSON response (HTTP ${resp.status})`, true);
  }

  if (!resp.ok || body.error) {
    // 4xx are caller errors (bad message shape) — not retryable.
    throw new AssistantError(body.error ?? `HTTP ${resp.status}`, resp.status >= 500);
  }
  return body;
}
