import { HttpResponse, http } from 'msw';
import { expect, it, vi } from 'vitest';

import { paperApi, ApiError, apiErrorMessage } from './client';
import { server } from '../test/server';
import type { Highlight, ModelProfile, Note } from './types';

const modelProfileFixture = (overrides: Partial<ModelProfile> = {}): ModelProfile => ({
  id: 'profile-a',
  display_name: '本地 Qwen',
  base_url: 'http://127.0.0.1:8001/v1',
  model_name: 'qwen3',
  enabled: true,
  is_default: true,
  revision: 3,
  has_api_key: false,
  api_key_mask: null,
  context_length: null,
  max_output_tokens: null,
  capabilities: {
    basic_chat: true,
    structured_output: true,
    tool_calling: false,
    checked_at: '2026-08-21T08:00:00Z',
  },
  read_only: false,
  ...overrides,
});

const noteFixture = (overrides: Partial<Note> = {}): Note => ({
  id: 'note-a',
  body: '这是一条笔记。',
  element_id: null,
  page_number: 2,
  note_type: 'manual',
  anchor_ids: [],
  model: null,
  ai_generated: false,
  user_edited: false,
  created_at: '2026-08-21T08:00:00Z',
  updated_at: '2026-08-21T09:00:00Z',
  ...overrides,
});

const highlightFixture = (overrides: Partial<Highlight> = {}): Highlight => ({
  id: 'highlight-a',
  color: 'yellow',
  anchor: {
    id: 'anchor-a',
    quote: '稀疏路由',
    page_number: 2,
    element_id: null,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }],
  },
  ...overrides,
});

it('sends paper upload as FormData and preserves a safe API error', async () => {
  server.use(http.post('/api/papers', () => HttpResponse.json(
    { detail: 'Invalid PDF upload.' }, { status: 422 },
  )));

  await expect(paperApi.upload(new File(['not-pdf'], 'paper.pdf')))
    .rejects.toMatchObject({ status: 422, message: '请求失败，请稍后重试。' });
});

it('forwards an AbortSignal to selected paper loads', async () => {
  const controller = new AbortController();
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(
    new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
  ));

  try {
    await Promise.all([
      paperApi.getDocument('paper id', { signal: controller.signal }),
      paperApi.getNotes('paper id', { signal: controller.signal }),
    ]);

    expect(fetchSpy).toHaveBeenNthCalledWith(1, '/api/papers/paper%20id/document', {
      signal: controller.signal,
    });
    expect(fetchSpy).toHaveBeenNthCalledWith(2, '/api/papers/paper%20id/notes', {
      signal: controller.signal,
    });
  } finally {
    fetchSpy.mockRestore();
  }
});

it('builds a relative original-PDF URL', () => {
  expect(paperApi.getSourceUrl('paper id')).toBe('/api/papers/paper%20id/source');
});

it('lists model profiles', async () => {
  const profile = modelProfileFixture();
  server.use(http.get('/api/model-profiles', () => HttpResponse.json([profile])));

  await expect(paperApi.listModelProfiles()).resolves.toEqual([profile]);
});

it('creates a model profile from a 201 response', async () => {
  const profile = modelProfileFixture();
  let receivedBody: unknown;
  server.use(http.post('/api/model-profiles', async ({ request }) => {
    receivedBody = await request.json();
    return HttpResponse.json(profile, { status: 201 });
  }));

  const input = {
    display_name: '本地 Qwen',
    base_url: 'http://127.0.0.1:8001/v1',
    model_name: 'qwen3',
    enabled: true,
    is_default: true,
    api_key: 'secret-key',
  };
  await expect(paperApi.createModelProfile(input)).resolves.toEqual(profile);
  expect(receivedBody).toEqual(input);
});

it('sends the If-Match revision when updating a model profile', async () => {
  const profile = modelProfileFixture({ revision: 4 });
  let receivedBody: unknown;
  let receivedRevision: string | null = null;
  server.use(http.patch('/api/model-profiles/profile-a', async ({ request }) => {
    receivedBody = await request.json();
    receivedRevision = request.headers.get('If-Match');
    return HttpResponse.json(profile);
  }));

  await expect(paperApi.updateModelProfile('profile-a', 3, { display_name: '新名称' }))
    .resolves.toEqual(profile);
  expect(receivedRevision).toBe('3');
  expect(receivedBody).toEqual({ display_name: '新名称' });
});

it('clears an api key by sending an explicit null without a clear_api_key field', async () => {
  const profile = modelProfileFixture({ has_api_key: false, revision: 4 });
  let receivedBody: unknown;
  server.use(http.patch('/api/model-profiles/profile-a', async ({ request }) => {
    receivedBody = await request.json();
    return HttpResponse.json(profile);
  }));

  await paperApi.updateModelProfile('profile-a', 3, { clear_api_key: true });

  expect(receivedBody).toEqual({ api_key: null });
  expect(receivedBody).not.toHaveProperty('clear_api_key');
});

it('sends the If-Match revision when deleting a model profile and resolves undefined on 204', async () => {
  let receivedRevision: string | null = null;
  server.use(http.delete('/api/model-profiles/profile-a', ({ request }) => {
    receivedRevision = request.headers.get('If-Match');
    return new HttpResponse(null, { status: 204 });
  }));

  await expect(paperApi.deleteModelProfile('profile-a', 3)).resolves.toBeUndefined();
  expect(receivedRevision).toBe('3');
});

it('sets the default model profile with the If-Match revision', async () => {
  const profile = modelProfileFixture({ is_default: true, revision: 4 });
  let receivedRevision: string | null = null;
  server.use(http.post('/api/model-profiles/profile-a/default', ({ request }) => {
    receivedRevision = request.headers.get('If-Match');
    return HttpResponse.json(profile);
  }));

  await expect(paperApi.setDefaultModelProfile('profile-a', 3)).resolves.toEqual(profile);
  expect(receivedRevision).toBe('3');
});

it('tests a model profile with the If-Match revision', async () => {
  const profile = modelProfileFixture({
    revision: 4,
    capabilities: {
      basic_chat: true,
      structured_output: true,
      tool_calling: true,
      checked_at: '2026-08-21T09:30:00Z',
    },
  });
  let receivedRevision: string | null = null;
  server.use(http.post('/api/model-profiles/profile-a/test', ({ request }) => {
    receivedRevision = request.headers.get('If-Match');
    return HttpResponse.json(profile);
  }));

  await expect(paperApi.testModelProfile('profile-a', 3)).resolves.toEqual(profile);
  expect(receivedRevision).toBe('3');
});

it('keeps server diagnostics internally but never exposes a Chinese detail as the public message', async () => {
  server.use(http.delete('/api/papers/paper-a', () => HttpResponse.json(
    { code: 'PAPER_BUSY', detail: '论文正在处理中。' },
    { status: 409 },
  )));

  await expect(paperApi.deletePaper('paper-a', 'paper-a')).rejects.toMatchObject({
    status: 409,
    code: 'PAPER_BUSY',
    detail: '论文正在处理中。',
    message: '请求失败，请稍后重试。',
  });
});

it('keeps a stable code while replacing an English server detail with Chinese public copy', async () => {
  server.use(http.get('/api/papers/paper-a', () => HttpResponse.json(
    { detail: 'Paper resource not found.' },
    { status: 404 },
  )));

  await expect(paperApi.getPaper('paper-a')).rejects.toMatchObject({
    status: 404,
    code: null,
    detail: 'Paper resource not found.',
    message: '请求失败，请稍后重试。',
  });
});

it('loads the annotation bundle for a paper', async () => {
  const bundle = { highlights: [highlightFixture()], notes: [noteFixture()] };
  server.use(http.get('/api/papers/paper-a/annotations', () => HttpResponse.json(bundle)));

  await expect(paperApi.getAnnotations('paper-a')).resolves.toEqual(bundle);
});

it('creates a highlight from a text anchor draft', async () => {
  const highlight = highlightFixture();
  let receivedBody: unknown;
  server.use(http.post('/api/papers/paper-a/highlights', async ({ request }) => {
    receivedBody = await request.json();
    return HttpResponse.json(highlight);
  }));

  const input = {
    quote: '稀疏路由',
    page_number: 2,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }],
    color: 'yellow' as const,
    request_id: 'request-a',
  };
  await expect(paperApi.createHighlight('paper-a', input)).resolves.toEqual(highlight);
  expect(receivedBody).toEqual(input);
});

it('creates a manual note with its durable anchor and idempotency request id', async () => {
  const note = noteFixture();
  let receivedBody: unknown;
  server.use(http.post('/api/papers/paper-a/notes', async ({ request }) => {
    receivedBody = await request.json();
    return HttpResponse.json(note);
  }));
  const input = {
    body: '手写锚定笔记', request_id: 'manual-note-a',
    anchor: { quote: '原文', page_number: 2, rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }] },
  };
  await expect(paperApi.createNote('paper-a', input)).resolves.toEqual(note);
  expect(receivedBody).toEqual(input);
});

it('deletes a highlight and resolves undefined on 204', async () => {
  server.use(http.delete(
    '/api/papers/paper-a/highlights/highlight-a',
    () => new HttpResponse(null, { status: 204 }),
  ));

  await expect(paperApi.deleteHighlight('paper-a', 'highlight-a')).resolves.toBeUndefined();
});

it('updates a note with the expected updated_at guard', async () => {
  const note = noteFixture({ body: '更新后的笔记。', user_edited: true });
  let receivedBody: unknown;
  server.use(http.patch('/api/papers/paper-a/notes/note-a', async ({ request }) => {
    receivedBody = await request.json();
    return HttpResponse.json(note);
  }));

  const input = { body: '更新后的笔记。', expected_updated_at: '2026-08-21T09:00:00Z' };
  await expect(paperApi.updateNote('paper-a', 'note-a', input)).resolves.toEqual(note);
  expect(receivedBody).toEqual(input);
});

it('deletes a note and resolves undefined on 204', async () => {
  server.use(http.delete(
    '/api/papers/paper-a/notes/note-a',
    () => new HttpResponse(null, { status: 204 }),
  ));

  await expect(paperApi.deleteNote('paper-a', 'note-a')).resolves.toBeUndefined();
});

it('deletes a paper with an explicit confirmation and resolves undefined on 204', async () => {
  let receivedBody: unknown;
  server.use(http.delete('/api/papers/paper-a', async ({ request }) => {
    receivedBody = await request.json();
    return new HttpResponse(null, { status: 204 });
  }));

  await expect(paperApi.deletePaper('paper-a', 'paper-a')).resolves.toBeUndefined();
  expect(receivedBody).toEqual({ confirmation: 'paper-a' });
});

it('reports the reason behind a failed stream instead of a generic fallback', () => {
  // Breaks if a known stream code is replaced by the caller's generic wording.
  expect(apiErrorMessage(new ApiError(0, '', 'agent_failed', null), '暂时无法获取助手回答。'))
    .toBe('模型未能完成有效回答，请重试或在模型设置中重新测试。');
  expect(apiErrorMessage(new ApiError(0, '', 'answer_unavailable', null), '暂时无法获取助手回答。'))
    .toBe('回答已生成，但引用信息无法解析，请重试。');
});

it('never echoes server-supplied error text for an unknown code', () => {
  // Breaks if an upstream message is surfaced verbatim in the reader.
  const upstream = new ApiError(502, '', 'UPSTREAM_TIMEOUT', 'upstream timeout at 10.0.0.7');

  expect(apiErrorMessage(upstream, '解释请求失败，请重试。')).toBe('解释请求失败，请重试。');
  expect(apiErrorMessage(new Error('raw transport failure'), '解释请求失败，请重试。'))
    .toBe('解释请求失败，请重试。');
});

