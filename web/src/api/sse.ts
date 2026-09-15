import { ApiError, readApiError } from './client';
import type {
  AgentStreamEvent,
  AskAgentInput,
  SelectionAssistEvent,
  SelectionAssistInput,
} from './types';

interface StreamConfig {
  known: ReadonlySet<string>;
  label: string;
}

const STREAM_EVENTS = new Set(['started', 'delta', 'completed', 'error']);
const ASSIST_STREAM: StreamConfig = { known: STREAM_EVENTS, label: 'Selection assist' };
const AGENT_STREAM: StreamConfig = { known: STREAM_EVENTS, label: 'Paper agent' };

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('The operation was aborted.', 'AbortError');
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw abortError(signal);
  }
}

function drainFrames(buffer: string): { frames: string[]; rest: string } {
  const frames: string[] = [];
  let rest = buffer;
  let match = /\r?\n\r?\n/.exec(rest);
  while (match !== null) {
    frames.push(rest.slice(0, match.index));
    rest = rest.slice(match.index + match[0].length);
    match = /\r?\n\r?\n/.exec(rest);
  }
  return { frames, rest };
}

function parseFrame(frame: string, config: StreamConfig): { event: string; data: unknown } | null {
  let event: string | null = null;
  const dataLines: string[] = [];
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    if (line === '' || line.startsWith(':')) {
      continue;
    }
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trimStart();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart());
    }
  }
  if (event === null && dataLines.length === 0) {
    return null;
  }
  if (event === null || !config.known.has(event)) {
    throw new ApiError(
      0,
      `${config.label} stream returned an unknown event.`,
      'unknown_event',
    );
  }
  let data: unknown;
  try {
    data = JSON.parse(dataLines.join('\n'));
  } catch {
    throw new ApiError(
      0,
      `${config.label} stream returned invalid event data.`,
      'invalid_event_data',
    );
  }
  return { event, data };
}

export async function* parseSse<T>(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  config: StreamConfig = ASSIST_STREAM,
): AsyncGenerator<T> {
  const reader = stream.getReader();
  const abortListener = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener('abort', abortListener, { once: true });
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      throwIfAborted(signal);
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const drained = drainFrames(buffer);
      buffer = drained.rest;
      for (const frame of drained.frames) {
        const event = parseFrame(frame, config);
        if (event !== null) {
          yield event as T;
        }
      }
    }
    throwIfAborted(signal);
    buffer += decoder.decode();
    if (buffer.trim() !== '') {
      const event = parseFrame(buffer, config);
      if (event !== null) {
        yield event as T;
      }
    }
  } catch (error) {
    if (signal?.aborted) {
      throw abortError(signal);
    }
    throw error;
  } finally {
    signal?.removeEventListener('abort', abortListener);
    reader.releaseLock();
  }
}

async function* streamEndpoint<T>(
  path: string,
  input: unknown,
  signal: AbortSignal,
  config: StreamConfig,
): AsyncGenerator<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
    signal,
  });

  if (!response.ok) {
    throw await readApiError(response);
  }

  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.toLowerCase().includes('text/event-stream')) {
    throw new ApiError(
      response.status,
      `${config.label} response is not an event stream.`,
      'invalid_content_type',
    );
  }

  if (response.body === null) {
    throw new ApiError(
      response.status,
      `${config.label} response has no body.`,
      'empty_event_stream',
    );
  }

  yield* parseSse<T>(response.body, signal, config);
}

export async function* streamSelectionAssist(
  paperId: string,
  input: SelectionAssistInput,
  signal: AbortSignal,
): AsyncGenerator<SelectionAssistEvent> {
  yield* streamEndpoint<SelectionAssistEvent>(
    `/api/papers/${encodeURIComponent(paperId)}/selection-assists`,
    input,
    signal,
    ASSIST_STREAM,
  );
}

export async function* streamAgentMessage(
  paperId: string,
  input: AskAgentInput,
  signal: AbortSignal,
): AsyncGenerator<AgentStreamEvent> {
  yield* streamEndpoint<AgentStreamEvent>(
    `/api/papers/${encodeURIComponent(paperId)}/agent/messages/stream`,
    input,
    signal,
    AGENT_STREAM,
  );
}
