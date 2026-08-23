import { ApiError, readApiError } from './client';
import type { SelectionAssistEvent, SelectionAssistInput } from './types';

const KNOWN_EVENTS = new Set(['started', 'delta', 'completed', 'error']);

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

function parseFrame(frame: string): SelectionAssistEvent | null {
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
  if (event === null || !KNOWN_EVENTS.has(event)) {
    throw new ApiError(
      0,
      'Selection assist stream returned an unknown event.',
      'unknown_event',
    );
  }
  let data: unknown;
  try {
    data = JSON.parse(dataLines.join('\n'));
  } catch {
    throw new ApiError(
      0,
      'Selection assist stream returned invalid event data.',
      'invalid_event_data',
    );
  }
  return { event, data } as SelectionAssistEvent;
}

export async function* parseSse(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SelectionAssistEvent> {
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
        const event = parseFrame(frame);
        if (event !== null) {
          yield event;
        }
      }
    }
    throwIfAborted(signal);
    buffer += decoder.decode();
    if (buffer.trim() !== '') {
      const event = parseFrame(buffer);
      if (event !== null) {
        yield event;
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

export async function* streamSelectionAssist(
  paperId: string,
  input: SelectionAssistInput,
  signal: AbortSignal,
): AsyncGenerator<SelectionAssistEvent> {
  const response = await fetch(
    `/api/papers/${encodeURIComponent(paperId)}/selection-assists`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
      signal,
    },
  );

  if (!response.ok) {
    throw await readApiError(response);
  }

  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.toLowerCase().includes('text/event-stream')) {
    throw new ApiError(
      response.status,
      'Selection assist response is not an event stream.',
      'invalid_content_type',
    );
  }

  if (response.body === null) {
    throw new ApiError(
      response.status,
      'Selection assist response has no body.',
      'empty_event_stream',
    );
  }

  yield* parseSse(response.body, signal);
}
