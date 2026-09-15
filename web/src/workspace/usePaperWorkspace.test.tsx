import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ApiError, paperApi } from '../api/client';
import { streamAgentMessage, streamSelectionAssist } from '../api/sse';
import type { AgentMessage, Highlight, Note, PaperDocument, SelectionAssistEvent, TextAnchorDraft } from '../api/types';
import { usePaperWorkspace } from './usePaperWorkspace';

vi.mock('../api/sse', () => ({ streamSelectionAssist: vi.fn(), streamAgentMessage: vi.fn() }));

const selectionDraft: TextAnchorDraft = {
  quote: '选中的原文', page_number: 1, rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
};

function documentFor(paperId: string): PaperDocument {
  return {
    paper: { id: paperId, original_filename: `${paperId}.pdf`, status: 'completed' },
    pages: [],
    sections: [],
    elements: [],
    notes: [],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(paperApi, 'getDocument').mockImplementation(async (paperId) => documentFor(paperId));
  vi.spyOn(paperApi, 'getNotes').mockResolvedValue([]);
  vi.spyOn(paperApi, 'getAnnotations').mockResolvedValue({ highlights: [], notes: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows a Chinese workspace fallback instead of raw transport or server errors', async () => {
  vi.spyOn(paperApi, 'getDocument').mockRejectedValueOnce(new ApiError(503, 'Server temporarily unavailable.', 'UPSTREAM_DOWN'));
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.errorMessage).toBe('论文阅读工作区暂时无法加载。'));
});

it('replaces an English SSE error detail with a Chinese public fallback', async () => {
  vi.mocked(streamSelectionAssist).mockImplementation(async function* (): AsyncGenerator<SelectionAssistEvent> {
    yield { event: 'error', data: { code: 'UPSTREAM_TIMEOUT', detail: '内部错误：Upstream timeout at 10.0.0.7' } };
  });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  let response: Awaited<ReturnType<typeof result.current.runSelectionAssist>> | undefined;
  await act(async () => {
    response = await result.current.runSelectionAssist('explain', selectionDraft, 'qwen', 'request-a', new AbortController().signal);
  });

  expect(response).toEqual({ status: 'failed', text: '', message: '解释请求失败，请重试。' });
});

it('never exposes a Chinese SSE error detail and uses the translate fallback', async () => {
  vi.mocked(streamSelectionAssist).mockImplementation(async function* (): AsyncGenerator<SelectionAssistEvent> {
    yield { event: 'error', data: { code: 'MODEL_BUSY', detail: '模型服务繁忙，请稍后重试。' } };
  });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  let response: Awaited<ReturnType<typeof result.current.runSelectionAssist>> | undefined;
  await act(async () => {
    response = await result.current.runSelectionAssist('translate', selectionDraft, 'qwen', 'request-b', new AbortController().signal);
  });

  expect(response).toEqual({ status: 'failed', text: '', message: '翻译请求失败，请重试。' });
});

it('reports an unfinished translate stream as a translate-specific failure', async () => {
  vi.mocked(streamSelectionAssist).mockImplementation(async function* (): AsyncGenerator<SelectionAssistEvent> {
    yield { event: 'started', data: { request_id: 'request-incomplete' } };
  });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  let response: Awaited<ReturnType<typeof result.current.runSelectionAssist>> | undefined;
  await act(async () => {
    response = await result.current.runSelectionAssist('translate', selectionDraft, 'qwen', 'request-incomplete', new AbortController().signal);
  });

  expect(response).toEqual({ status: 'failed', text: '', message: '翻译请求未完成。' });
});

it('uses a fresh current-model payload for Agent calls without resetting the conversation', async () => {
  const first: AgentMessage = { conversation_id: 'conversation-a', message_id: 'message-a', status: 'grounded', paper_answer: '回答一', background_explanation: null, citations: [] };
  const second: AgentMessage = { ...first, message_id: 'message-b', paper_answer: '回答二' };
  const agent = vi.mocked(streamAgentMessage)
    .mockImplementationOnce(async function* () {
      yield { event: 'started', data: { request_id: 'agent-request-a' } };
      yield { event: 'delta', data: { text: '回答一' } };
      yield { event: 'completed', data: { message: first } };
    })
    .mockImplementationOnce(async function* () {
      yield { event: 'started', data: { request_id: 'agent-request-b' } };
      yield { event: 'completed', data: { message: second } };
    });
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('agent-request-a').mockReturnValueOnce('agent-request-b') });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  await act(async () => { await result.current.askAgent('第一个问题', 'qwen'); });
  await act(async () => { await result.current.askAgent('第二个问题', 'deepseek', { quote: '选择原文', page_number: 2, rects: [] }); });

  expect(agent).toHaveBeenNthCalledWith(1, 'paper-a', { content: '第一个问题', conversation_id: undefined, model_profile_id: 'qwen', request_id: 'agent-request-a' }, expect.any(AbortSignal));
  expect(agent).toHaveBeenNthCalledWith(2, 'paper-a', { content: '第二个问题', conversation_id: 'conversation-a', model_profile_id: 'deepseek', request_id: 'agent-request-b', selection: { quote: '选择原文', page_number: 2, rects: [] } }, expect.any(AbortSignal));
  expect(result.current.conversationId).toBe('conversation-a');
  expect(result.current.messages.map(({ message_id }) => message_id)).toEqual(['message-a', 'message-b']);
  expect(result.current.exchanges).toEqual([
    { question: '第一个问题', message: first },
    { question: '第二个问题', message: second },
  ]);
  expect(result.current.streaming).toBeNull();
});

it('exposes streamed answer text until the turn is completed', async () => {
  const answer: AgentMessage = { conversation_id: 'conversation-a', message_id: 'message-a', status: 'grounded', paper_answer: '完整回答', background_explanation: null, citations: [] };
  let release: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.mocked(streamAgentMessage).mockImplementationOnce(async function* () {
    yield { event: 'started', data: { request_id: 'agent-request-a' } };
    yield { event: 'delta', data: { text: '完整' } };
    await gate;
    yield { event: 'delta', data: { text: '回答' } };
    yield { event: 'completed', data: { message: answer } };
  });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  let pending: Promise<AgentMessage | null> = Promise.resolve(null);
  await act(async () => {
    pending = result.current.askAgent('问题', 'qwen');
  });

  expect(result.current.streaming).toEqual({ question: '问题', text: '完整', interrupted: false });
  expect(result.current.exchanges).toEqual([]);

  await act(async () => {
    release?.();
    await pending;
  });

  expect(result.current.streaming).toBeNull();
  expect(result.current.exchanges).toEqual([{ question: '问题', message: answer }]);
});

it('keeps the streamed answer and reports the API reason when the stream errors', async () => {
  vi.mocked(streamAgentMessage).mockImplementationOnce(async function* () {
    yield { event: 'started', data: { request_id: 'agent-request-a' } };
    yield { event: 'delta', data: { text: '半截回答' } };
    yield { event: 'error', data: { code: 'agent_failed', detail: '模型未能完成有效回答。' } };
  });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  await act(async () => { await result.current.askAgent('问题', 'qwen'); });

  expect(result.current.streaming).toEqual({ question: '问题', text: '半截回答', interrupted: true });
  expect(result.current.exchanges).toEqual([]);
  expect(result.current.errorMessage).toBe('模型未能完成有效回答，请重试或在模型设置中重新测试。');
});

it('loads highlights independently from the existing notes request', async () => {
  const highlight: Highlight = {
    id: 'highlight-a',
    color: 'yellow',
    anchor: {
      id: 'anchor-a', quote: '选中的文字', page_number: 1, element_id: null,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
    },
  };
  vi.spyOn(paperApi, 'getAnnotations').mockResolvedValue({ highlights: [highlight], notes: [] });

  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.highlights).toEqual([highlight]));
  expect(paperApi.getNotes).toHaveBeenCalledWith('paper-a', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(paperApi.getAnnotations).toHaveBeenCalledWith('paper-a', expect.objectContaining({ signal: expect.any(AbortSignal) }));
});

it('keeps the annotation bundle anchor map for durable note source positioning', async () => {
  vi.spyOn(paperApi, 'getAnnotations').mockResolvedValue({
    highlights: [],
    notes: [],
    anchors: [{
      id: 'note-anchor', quote: '持久原文', page_number: 2, element_id: null,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }],
    }],
  });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.anchors).toEqual([
    expect.objectContaining({ id: 'note-anchor', quote: '持久原文' }),
  ]));
});

it('does not let a stale annotation load replace a highlight created after the load started', async () => {
  const pendingAnnotations = deferred<{ highlights: Highlight[]; notes: Note[] }>();
  const createdHighlight: Highlight = {
    id: 'created-highlight', color: 'yellow',
    anchor: {
      id: 'created-anchor', quote: '新高亮', page_number: 1, element_id: null,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 }],
    },
  };
  vi.mocked(paperApi.getAnnotations).mockReturnValue(pendingAnnotations.promise);
  vi.spyOn(paperApi, 'createHighlight').mockResolvedValue(createdHighlight);
  vi.stubGlobal('crypto', { randomUUID: () => 'create-request' });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(paperApi.getAnnotations).toHaveBeenCalledOnce());

  act(() => result.current.setSelection({
    quote: '新高亮', page_number: 1,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 }],
  }, { left: 20, top: 30, width: 40, height: 10 } as DOMRect));
  await act(async () => { await result.current.createHighlight(); });
  await act(async () => {
    pendingAnnotations.resolve({ highlights: [], notes: [] });
    await Promise.resolve();
  });

  expect(result.current.highlights).toEqual([createdHighlight]);
});

it('does not let a stale annotation load reintroduce a deleted highlight', async () => {
  const pendingAnnotations = deferred<{ highlights: Highlight[]; notes: Note[] }>();
  const highlight: Highlight = {
    id: 'deleted-highlight', color: 'yellow',
    anchor: {
      id: 'deleted-anchor', quote: '待删除高亮', page_number: 1, element_id: null,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 }],
    },
  };
  vi.mocked(paperApi.getAnnotations).mockReturnValue(pendingAnnotations.promise);
  vi.spyOn(paperApi, 'createHighlight').mockResolvedValue(highlight);
  vi.spyOn(paperApi, 'deleteHighlight').mockResolvedValue(undefined);
  vi.stubGlobal('crypto', { randomUUID: () => 'create-request' });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(paperApi.getAnnotations).toHaveBeenCalledOnce());

  act(() => result.current.setSelection({
    quote: '待删除高亮', page_number: 1,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 }],
  }, { left: 20, top: 30, width: 40, height: 10 } as DOMRect));
  await act(async () => { await result.current.createHighlight(); });
  await act(async () => { await result.current.deleteHighlight(highlight.id); });
  await act(async () => {
    pendingAnnotations.resolve({ highlights: [highlight], notes: [] });
    await Promise.resolve();
  });

  expect(result.current.highlights).toEqual([]);
});

it('persists a yellow highlight with a fresh request id and clears the selection on success', async () => {
  const highlight: Highlight = {
    id: 'highlight-a', color: 'yellow',
    anchor: {
      id: 'anchor-a', quote: '选中的文字', page_number: 1, element_id: null,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
    },
  };
  vi.spyOn(paperApi, 'createHighlight').mockResolvedValue(highlight);
  vi.stubGlobal('crypto', { randomUUID: () => 'request-highlight-a' });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  act(() => result.current.setSelection({
    quote: '选中的文字', page_number: 1,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
  }, { left: 20, top: 30, width: 40, height: 10 } as DOMRect));
  await act(async () => { await result.current.createHighlight(); });

  expect(paperApi.createHighlight).toHaveBeenCalledWith('paper-a', {
    quote: '选中的文字', page_number: 1,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
    color: 'yellow', request_id: 'request-highlight-a',
  });
  expect(result.current.highlights).toEqual([highlight]);
  expect(result.current.selection).toBeNull();
});

it('keeps the selection and uses a local error when highlight persistence fails', async () => {
  vi.spyOn(paperApi, 'createHighlight').mockRejectedValue(new ApiError(503, '服务不可用。'));
  vi.stubGlobal('crypto', { randomUUID: () => 'request-highlight-a' });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  act(() => result.current.setSelection({
    quote: '选中的文字', page_number: 1,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
  }, { left: 20, top: 30, width: 40, height: 10 } as DOMRect));
  await act(async () => { await result.current.createHighlight(); });

  expect(result.current.selection?.draft.quote).toBe('选中的文字');
  expect(result.current.errorMessage).toBe('高亮保存失败：请稍后重试。');
});

it('updates a highlight color and reports a failed color change safely', async () => {
  const yellowHighlight: Highlight = {
    id: 'highlight-a', color: 'yellow',
    anchor: {
      id: 'anchor-a', quote: '选中的文字', page_number: 1, element_id: null,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
    },
  };
  const greenHighlight: Highlight = { ...yellowHighlight, color: 'green' };
  vi.spyOn(paperApi, 'getAnnotations').mockResolvedValue({ highlights: [yellowHighlight], notes: [] });
  vi.spyOn(paperApi, 'updateHighlight').mockResolvedValue(greenHighlight);
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.highlights).toEqual([yellowHighlight]));

  await act(async () => { await result.current.changeHighlightColor('highlight-a', 'green'); });

  expect(paperApi.updateHighlight).toHaveBeenCalledWith(
    'paper-a', 'highlight-a', { color: 'green' },
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(result.current.highlights).toEqual([greenHighlight]);

  vi.mocked(paperApi.updateHighlight).mockRejectedValueOnce(new ApiError(503, '服务不可用。'));
  await act(async () => { await result.current.changeHighlightColor('highlight-a', 'blue'); });

  expect(result.current.highlights).toEqual([greenHighlight]);
  expect(result.current.errorMessage).toBe('修改高亮颜色失败：请稍后重试。');
});

it('mounts required document state when notes fail independently', async () => {
  vi.mocked(paperApi.getNotes).mockRejectedValue(
    new ApiError(503, 'Notes are temporarily unavailable.'),
  );

  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.document).not.toBeNull());
  expect(result.current.errorMessage).toBeNull();
  expect(result.current.notesErrorMessage).toBe('论文笔记暂时无法加载。');
});

it('mounts required document state while notes remain unresolved', async () => {
  const pendingNotes = deferred<Note[]>();
  vi.mocked(paperApi.getNotes).mockReturnValue(pendingNotes.promise);

  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.document).not.toBeNull());
  expect(result.current.notes).toEqual([]);
});

it('ignores a stale notes response after retrying the same paper', async () => {
  const staleNotes = deferred<Note[]>();
  const currentNote: Note = {
    id: 'current-note', body: 'Current retry note.', element_id: null, page_number: null,
  };
  vi.mocked(paperApi.getNotes)
    .mockReturnValueOnce(staleNotes.promise)
    .mockResolvedValueOnce([currentNote]);

  const { result, rerender } = renderHook(
    ({ revision }) => usePaperWorkspace('paper-a', revision),
    { initialProps: { revision: 0 } },
  );
  await waitFor(() => expect(result.current.document).not.toBeNull());

  rerender({ revision: 1 });
  await waitFor(() => expect(result.current.notes).toEqual([currentNote]));
  expect(paperApi.getDocument).toHaveBeenCalledTimes(2);
  expect(paperApi.getNotes).toHaveBeenCalledTimes(2);
  await act(async () => {
    staleNotes.resolve([{
      id: 'stale-note', body: 'Obsolete retry note.', element_id: null, page_number: null,
    }]);
    await Promise.resolve();
  });

  expect(result.current.notes).toEqual([currentNote]);
});

it('ignores a stale notes response after switching papers', async () => {
  const staleNotes = deferred<Note[]>();
  const paperBNote: Note = {
    id: 'paper-b-note', body: 'Paper B note.', element_id: null, page_number: null,
  };
  vi.mocked(paperApi.getNotes)
    .mockReturnValueOnce(staleNotes.promise)
    .mockResolvedValueOnce([paperBNote]);

  const { result, rerender } = renderHook(
    ({ paperId }) => usePaperWorkspace(paperId),
    { initialProps: { paperId: 'paper-a' as string | null } },
  );
  rerender({ paperId: 'paper-b' });
  await waitFor(() => expect(result.current.notes).toEqual([paperBNote]));
  await act(async () => {
    staleNotes.resolve([{
      id: 'paper-a-note', body: 'Paper A note.', element_id: null, page_number: null,
    }]);
    await Promise.resolve();
  });

  expect(result.current.activePaperId).toBe('paper-b');
  expect(result.current.notes).toEqual([paperBNote]);
});

it('ignores old Agent and note completions after retrying the same paper', async () => {
  const staleMessage: AgentMessage = {
    conversation_id: 'stale-conversation',
    message_id: 'stale-message',
    status: 'grounded',
    paper_answer: 'Old answer.',
    background_explanation: null,
    citations: [],
  };
  const gate = deferred<void>();
  const oldNote = deferred<Note>();
  vi.mocked(streamAgentMessage).mockImplementationOnce(async function* () {
    yield { event: 'started', data: { request_id: 'agent-request-a' } };
    await gate.promise;
    yield { event: 'completed', data: { message: staleMessage } };
  });
  vi.spyOn(paperApi, 'createNote').mockReturnValue(oldNote.promise);

  const { result, rerender } = renderHook(
    ({ revision }) => usePaperWorkspace('paper-a', revision),
    { initialProps: { revision: 0 } },
  );
  await waitFor(() => expect(result.current.document).not.toBeNull());

  const agentRequest = result.current.askAgent('Old question.', 'qwen');
  const noteRequest = result.current.saveNote('Old note.');

  rerender({ revision: 1 });
  await waitFor(() => expect(result.current.loadRevision).toBe(1));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  const staleNote: Note = {
    id: 'stale-note', body: 'Old note.', element_id: null, page_number: null,
  };

  await act(async () => {
    gate.resolve(undefined);
    oldNote.resolve(staleNote);
    await Promise.all([agentRequest, noteRequest]);
  });

  expect(result.current.conversationId).toBeNull();
  expect(result.current.messages).toEqual([]);
  expect(result.current.notes).toEqual([]);
});

it('ignores a superseded stream failure so the newer answer stands', async () => {
  const answer: AgentMessage = { conversation_id: 'conversation-a', message_id: 'message-a', status: 'grounded', paper_answer: '新回答', background_explanation: null, citations: [] };
  const gate = deferred<void>();
  vi.mocked(streamAgentMessage)
    .mockImplementationOnce(async function* () {
      yield { event: 'started', data: { request_id: 'agent-request-a' } };
      yield { event: 'delta', data: { text: '半截' } };
      await gate.promise;
      throw new ApiError(503, 'stale stream failure');
    })
    .mockImplementationOnce(async function* () {
      yield { event: 'started', data: { request_id: 'agent-request-b' } };
      yield { event: 'delta', data: { text: '新回答' } };
      yield { event: 'completed', data: { message: answer } };
    });
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  let first: Promise<AgentMessage | null> = Promise.resolve(null);
  let second: Promise<AgentMessage | null> = Promise.resolve(null);
  await act(async () => {
    first = result.current.askAgent('第一个问题', 'qwen');
    second = result.current.askAgent('第二个问题', 'qwen');
    await second;
  });
  await act(async () => {
    gate.resolve(undefined);
    await first;
  });

  expect(result.current.errorMessage).toBeNull();
  expect(result.current.streaming).toBeNull();
  expect(result.current.exchanges).toEqual([{ question: '第二个问题', message: answer }]);
});

it('ignores an old mutation failure after retrying the same paper', async () => {
  const apiError = new ApiError(503, 'Old agent failure.');
  const gate = deferred<void>();
  vi.mocked(streamAgentMessage).mockImplementationOnce(async function* () {
    yield { event: 'started', data: { request_id: 'agent-request-a' } };
    await gate.promise;
    throw apiError;
  });

  const { result, rerender } = renderHook(
    ({ revision }) => usePaperWorkspace('paper-a', revision),
    { initialProps: { revision: 0 } },
  );
  await waitFor(() => expect(result.current.document).not.toBeNull());
  const oldRequest = result.current.askAgent('Old question.', 'qwen');
  const settledOldRequest = oldRequest.then((value) => value, (error: unknown) => error);

  rerender({ revision: 1 });
  await waitFor(() => expect(result.current.loadRevision).toBe(1));
  await act(async () => {
    gate.resolve(undefined);
    await settledOldRequest;
  });

  expect(await settledOldRequest).toBeNull();
  expect(result.current.errorMessage).toBeNull();
});
