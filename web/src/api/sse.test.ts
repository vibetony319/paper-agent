import { HttpResponse, http } from 'msw';
import { expect, it } from 'vitest';

import { ApiError } from './client';
import { parseSse, streamSelectionAssist } from './sse';
import { server } from '../test/server';
import type { SelectionAssistInput } from './types';

function streamFromChunks(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

async function collect<T>(events: AsyncIterable<T>): Promise<T[]> {
  const collected: T[] = [];
  for await (const event of events) {
    collected.push(event);
  }
  return collected;
}

const assistInput: SelectionAssistInput = {
  action: 'explain',
  model_profile_id: 'profile-a',
  request_id: 'request-a',
  quote: '稀疏路由',
  page_number: 2,
  rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }],
};

function sseResponse(frames: Array<[string, unknown]>): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const [event, data] of frames) {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
        );
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

it('parses split utf-8 sse chunks without losing chinese text', async () => {
  const bytes = new TextEncoder().encode(
    'event: delta\ndata: {"text":"论文解释"}\n\nevent: completed\ndata: {"note":{"id":"note-a"}}\n\n',
  );
  const stream = streamFromChunks([bytes.slice(0, 19), bytes.slice(19, 31), bytes.slice(31)]);
  const events = await collect(parseSse(stream));
  expect(events).toEqual([
    { event: 'delta', data: { text: '论文解释' } },
    { event: 'completed', data: { note: { id: 'note-a' } } },
  ]);
});

it('rejects unknown sse events with a safe api error', async () => {
  const bytes = new TextEncoder().encode('event: progress\ndata: {"text":"x"}\n\n');

  await expect(collect(parseSse(streamFromChunks([bytes])))).rejects.toMatchObject({
    name: 'ApiError',
    code: 'unknown_event',
  });
});

it('rejects sse frames with invalid json data', async () => {
  const bytes = new TextEncoder().encode('event: delta\ndata: {not-json}\n\n');

  await expect(collect(parseSse(streamFromChunks([bytes]))))
    .rejects.toBeInstanceOf(ApiError);
});

it('streams selection assist events from the paper endpoint', async () => {
  let receivedBody: unknown;
  server.use(http.post('/api/papers/paper-a/selection-assists', async ({ request }) => {
    receivedBody = await request.json();
    return sseResponse([
      ['started', { request_id: 'request-a' }],
      ['delta', { text: '这是' }],
      ['delta', { text: '解释。' }],
      ['completed', { note: { id: 'note-a' } }],
    ]);
  }));

  const controller = new AbortController();
  const events = await collect(streamSelectionAssist('paper-a', assistInput, controller.signal));

  expect(receivedBody).toEqual(assistInput);
  expect(events).toEqual([
    { event: 'started', data: { request_id: 'request-a' } },
    { event: 'delta', data: { text: '这是' } },
    { event: 'delta', data: { text: '解释。' } },
    { event: 'completed', data: { note: { id: 'note-a' } } },
  ]);
});

it('rejects a selection assist response that is not an event stream', async () => {
  server.use(http.post('/api/papers/paper-a/selection-assists', () => HttpResponse.json(
    { ok: true },
  )));

  const controller = new AbortController();
  await expect(collect(streamSelectionAssist('paper-a', assistInput, controller.signal)))
    .rejects.toMatchObject({ name: 'ApiError', code: 'invalid_content_type' });
});

it('keeps the api error code for failed selection assist requests', async () => {
  server.use(http.post('/api/papers/paper-a/selection-assists', () => HttpResponse.json(
    { code: 'MODEL_CONNECTION_FAILED', detail: '模型服务连接失败。' },
    { status: 503 },
  )));

  const controller = new AbortController();
  await expect(collect(streamSelectionAssist('paper-a', assistInput, controller.signal)))
    .rejects.toMatchObject({
      status: 503,
      code: 'MODEL_CONNECTION_FAILED',
      message: '模型服务连接失败。',
    });
});

it('stops reading and throws AbortError when the caller aborts', async () => {
  const encoder = new TextEncoder();
  server.use(http.post('/api/papers/paper-a/selection-assists', () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode('event: started\ndata: {"request_id":"request-a"}\n\n'),
        );
        // Never closes, simulating a hung generation.
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }));

  const controller = new AbortController();
  const events = streamSelectionAssist('paper-a', assistInput, controller.signal);

  const first = await events.next();
  expect(first).toEqual({
    done: false,
    value: { event: 'started', data: { request_id: 'request-a' } },
  });

  const pending = events.next();
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
});
