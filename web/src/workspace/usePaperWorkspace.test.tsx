import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ApiError, paperApi } from '../api/client';
import type { AgentMessage, Highlight, Note, PaperDocument, PaperGraph } from '../api/types';
import { usePaperWorkspace } from './usePaperWorkspace';

const emptyGraph: PaperGraph = { nodes: [], edges: [] };
const builtGraph: PaperGraph = {
  nodes: [{
    id: 'built', node_type: 'claim', name: 'Built graph', summary: 'Workspace result.',
    stage: 'stage2', evidence_element_ids: [],
  }],
  edges: [],
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
  vi.spyOn(paperApi, 'getGraph').mockResolvedValue(emptyGraph);
  vi.spyOn(paperApi, 'getNotes').mockResolvedValue([]);
  vi.spyOn(paperApi, 'getAnnotations').mockResolvedValue({ highlights: [], notes: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('returns and stores a core graph built for the active paper', async () => {
  const buildCoreGraph = vi.spyOn(paperApi, 'buildCoreGraph').mockResolvedValue(builtGraph);
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.graph).toBe(emptyGraph));

  let returnedGraph: PaperGraph | null | undefined;
  await act(async () => {
    returnedGraph = await result.current.buildCoreGraph();
  });

  expect(buildCoreGraph).toHaveBeenCalledWith('paper-a');
  expect(returnedGraph).toBe(builtGraph);
  expect(result.current.graph).toBe(builtGraph);
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

it('keeps the selection and exposes a Chinese error when highlight persistence fails', async () => {
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
  expect(result.current.errorMessage).toBe('高亮保存失败：服务不可用。');
});

it('starts a new Agent conversation when the second request changes answer scope', async () => {
  const paperOnlyResponse: AgentMessage = {
    conversation_id: 'paper-only-conversation',
    message_id: 'message-1',
    status: 'grounded',
    paper_answer: 'Paper-only answer.',
    background_explanation: null,
    citations: [],
  };
  const backgroundResponse: AgentMessage = {
    ...paperOnlyResponse,
    conversation_id: 'background-conversation',
    message_id: 'message-2',
  };
  const continuedBackgroundResponse: AgentMessage = {
    ...backgroundResponse,
    message_id: 'message-3',
  };
  const askAgent = vi.spyOn(paperApi, 'askAgent')
    .mockResolvedValueOnce(paperOnlyResponse)
    .mockResolvedValueOnce(backgroundResponse)
    .mockResolvedValueOnce(continuedBackgroundResponse);
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  await act(async () => {
    await result.current.askAgent('What does the paper show?', 'paper_only');
  });
  await waitFor(() => expect(result.current.conversationId).toBe('paper-only-conversation'));
  await act(async () => {
    await result.current.askAgent('Why does that matter?', 'external_knowledge');
  });
  await waitFor(() => expect(result.current.conversationId).toBe('background-conversation'));
  await act(async () => {
    await result.current.askAgent('What follows from that?', 'external_knowledge');
  });

  expect(askAgent).toHaveBeenNthCalledWith(1, 'paper-a', {
    content: 'What does the paper show?',
    mode: 'paper_only',
    conversation_id: undefined,
  });
  expect(askAgent).toHaveBeenNthCalledWith(2, 'paper-a', {
    content: 'Why does that matter?',
    mode: 'external_knowledge',
    conversation_id: undefined,
  });
  expect(askAgent).toHaveBeenNthCalledWith(3, 'paper-a', {
    content: 'What follows from that?',
    mode: 'external_knowledge',
    conversation_id: 'background-conversation',
  });
  expect(result.current.conversationId).toBe('background-conversation');
});

it('returns null without calling a builder when no paper is active', async () => {
  const buildCoreGraph = vi.spyOn(paperApi, 'buildCoreGraph');
  const buildDeepGraph = vi.spyOn(paperApi, 'buildDeepGraph');
  const { result } = renderHook(() => usePaperWorkspace(null));

  let coreResult: PaperGraph | null | undefined;
  let deepResult: PaperGraph | null | undefined;
  await act(async () => {
    coreResult = await result.current.buildCoreGraph();
    deepResult = await result.current.buildDeepGraph();
  });

  expect(coreResult).toBeNull();
  expect(deepResult).toBeNull();
  expect(buildCoreGraph).not.toHaveBeenCalled();
  expect(buildDeepGraph).not.toHaveBeenCalled();
});

it('reports and rethrows a deep graph API error', async () => {
  const apiError = new ApiError(503, 'Graph service is unavailable.');
  vi.spyOn(paperApi, 'buildDeepGraph').mockRejectedValue(apiError);
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.graph).toBe(emptyGraph));

  let caught: unknown;
  await act(async () => {
    try {
      await result.current.buildDeepGraph();
    } catch (error) {
      caught = error;
    }
  });

  expect(caught).toBe(apiError);
  expect(result.current.errorMessage).toBe('Graph service is unavailable.');
  expect(result.current.graph).toBe(emptyGraph);
});

it('mounts required document and graph state when notes fail independently', async () => {
  vi.mocked(paperApi.getNotes).mockRejectedValue(
    new ApiError(503, 'Notes are temporarily unavailable.'),
  );

  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.document).not.toBeNull());
  expect(result.current.graph).toBe(emptyGraph);
  expect(result.current.errorMessage).toBeNull();
  expect(result.current.notesErrorMessage).toBe('Notes are temporarily unavailable.');
});

it('mounts required document and graph state while notes remain unresolved', async () => {
  const pendingNotes = deferred<Note[]>();
  vi.mocked(paperApi.getNotes).mockReturnValue(pendingNotes.promise);

  const { result } = renderHook(() => usePaperWorkspace('paper-a'));

  await waitFor(() => expect(result.current.document).not.toBeNull());
  expect(result.current.graph).toBe(emptyGraph);
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
  expect(paperApi.getGraph).toHaveBeenCalledTimes(2);
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

it('ignores old graph, Agent, and note completions after retrying the same paper', async () => {
  const oldCoreGraph = deferred<PaperGraph>();
  const oldDeepGraph = deferred<PaperGraph>();
  const oldAgentMessage = deferred<AgentMessage>();
  const oldNote = deferred<Note>();
  vi.spyOn(paperApi, 'buildCoreGraph').mockReturnValue(oldCoreGraph.promise);
  vi.spyOn(paperApi, 'buildDeepGraph').mockReturnValue(oldDeepGraph.promise);
  vi.spyOn(paperApi, 'askAgent').mockReturnValue(oldAgentMessage.promise);
  vi.spyOn(paperApi, 'createNote').mockReturnValue(oldNote.promise);

  const { result, rerender } = renderHook(
    ({ revision }) => usePaperWorkspace('paper-a', revision),
    { initialProps: { revision: 0 } },
  );
  await waitFor(() => expect(result.current.document).not.toBeNull());

  const coreRequest = result.current.buildCoreGraph();
  const deepRequest = result.current.buildDeepGraph();
  const agentRequest = result.current.askAgent('Old question.', 'paper_only');
  const noteRequest = result.current.saveNote('Old note.');

  rerender({ revision: 1 });
  await waitFor(() => expect(result.current.loadRevision).toBe(1));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  const staleGraph: PaperGraph = {
    nodes: [{
      id: 'stale-node', node_type: 'claim', name: 'Stale graph', summary: 'Old result.',
      stage: 'stage2', evidence_element_ids: [],
    }],
    edges: [],
  };
  const staleMessage: AgentMessage = {
    conversation_id: 'stale-conversation',
    message_id: 'stale-message',
    status: 'grounded',
    paper_answer: 'Old answer.',
    background_explanation: null,
    citations: [],
  };
  const staleNote: Note = {
    id: 'stale-note', body: 'Old note.', element_id: null, page_number: null,
  };

  await act(async () => {
    oldCoreGraph.resolve(staleGraph);
    oldDeepGraph.resolve(staleGraph);
    oldAgentMessage.resolve(staleMessage);
    oldNote.resolve(staleNote);
    await Promise.all([coreRequest, deepRequest, agentRequest, noteRequest]);
  });

  expect(result.current.graph).toBe(emptyGraph);
  expect(result.current.conversationId).toBeNull();
  expect(result.current.messages).toEqual([]);
  expect(result.current.notes).toEqual([]);
});

it('ignores an obsolete A graph completion after retrying A, switching to B, and returning to A', async () => {
  const obsoleteGraph = deferred<PaperGraph>();
  vi.spyOn(paperApi, 'buildCoreGraph').mockReturnValue(obsoleteGraph.promise);

  const { result, rerender } = renderHook(
    ({ paperId, revision }) => usePaperWorkspace(paperId, revision),
    { initialProps: { paperId: 'paper-a' as string | null, revision: 0 } },
  );
  await waitFor(() => expect(result.current.document).not.toBeNull());
  const obsoleteRequest = result.current.buildCoreGraph();

  rerender({ paperId: 'paper-a', revision: 1 });
  await waitFor(() => expect(result.current.loadRevision).toBe(1));
  await waitFor(() => expect(result.current.document).not.toBeNull());

  rerender({ paperId: 'paper-b', revision: 0 });
  await waitFor(() => expect(result.current.activePaperId).toBe('paper-b'));
  await waitFor(() => expect(result.current.document?.paper.id).toBe('paper-b'));

  rerender({ paperId: 'paper-a', revision: 0 });
  await waitFor(() => expect(result.current.activePaperId).toBe('paper-a'));
  await waitFor(() => expect(result.current.document?.paper.id).toBe('paper-a'));

  await act(async () => {
    obsoleteGraph.resolve({
      nodes: [{
        id: 'obsolete-node', node_type: 'claim', name: 'Obsolete graph', summary: 'Old result.',
        stage: 'stage2', evidence_element_ids: [],
      }],
      edges: [],
    });
    await obsoleteRequest;
  });

  expect(result.current.graph).toBe(emptyGraph);
});

it('ignores an old mutation failure after retrying the same paper', async () => {
  const oldDeepGraph = deferred<PaperGraph>();
  const apiError = new ApiError(503, 'Old graph failure.');
  vi.spyOn(paperApi, 'buildDeepGraph').mockReturnValue(oldDeepGraph.promise);

  const { result, rerender } = renderHook(
    ({ revision }) => usePaperWorkspace('paper-a', revision),
    { initialProps: { revision: 0 } },
  );
  await waitFor(() => expect(result.current.document).not.toBeNull());
  const oldRequest = result.current.buildDeepGraph();
  const settledOldRequest = oldRequest.catch((error: unknown) => error);

  rerender({ revision: 1 });
  await waitFor(() => expect(result.current.loadRevision).toBe(1));
  await act(async () => {
    oldDeepGraph.reject(apiError);
    await settledOldRequest;
  });

  expect(await settledOldRequest).toBe(apiError);
  expect(result.current.errorMessage).toBeNull();
});
