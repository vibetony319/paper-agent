import { expect, it } from 'vitest';

import type { DocumentElement } from '../api/types';
import {
  initialWorkspaceState,
  toSourceTarget,
  workspaceReducer,
} from './reducer';
import type { WorkspaceState } from './types';

const locatedElement: DocumentElement = {
  id: 'element-a',
  kind: 'paragraph',
  text: 'Located evidence.',
  page_number: 2,
  bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
  section_id: 'section-a',
  location_status: 'located',
  order: 1,
};

const unlocatedElement: DocumentElement = {
  ...locatedElement,
  id: 'element-b',
  page_number: null,
  bbox: null,
  location_status: 'unlocated',
};

function readyWorkspace({
  paperId,
  conversationId,
}: {
  paperId: string;
  conversationId: string;
}): WorkspaceState {
  return {
    ...initialWorkspaceState,
    activePaperId: paperId,
    loadRevision: 0,
    document: {
      paper: { id: paperId, original_filename: 'paper.pdf', status: 'completed' },
      pages: [],
      sections: [],
      elements: [locatedElement],
      notes: [],
    },
    graph: { nodes: [], edges: [] },
    notes: [{ id: 'note-a', body: 'Old note.', element_id: 'element-a', page_number: 2 }],
    activeSource: {
      id: 'element-a',
      kind: 'paragraph',
      pageNumber: 2,
      bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
    },
    graphFocusNodeId: 'node-a',
    conversationId,
    conversationMode: 'paper_only',
    messages: [{
      conversation_id: conversationId,
      message_id: 'message-a',
      status: 'grounded',
      paper_answer: 'Old answer.',
      background_explanation: null,
      citations: [],
    }],
    notesErrorMessage: null,
  };
}

it('clears paper-specific workspace data when a different paper opens', () => {
  const state = readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' });

  const next = workspaceReducer(state, {
    type: 'paper/opened',
    paperId: 'paper-b',
    loadRevision: 0,
  });

  expect(next).toMatchObject({
    activePaperId: 'paper-b',
    document: null,
    graph: null,
    notes: [],
    activeSource: null,
    graphFocusNodeId: null,
    conversationId: null,
    conversationMode: null,
    messages: [],
    notesErrorMessage: null,
  });
});

it('rejects a stale notes load after a local note mutation', () => {
  const state = { ...initialWorkspaceState, activePaperId: 'paper-a', loadRevision: 3, notesMutationGeneration: 1, notes: [{ id: 'local', body: '本地结果', element_id: null, page_number: null }] };
  const next = workspaceReducer(state, { type: 'notes/loaded', paperId: 'paper-a', loadRevision: 3, mutationGeneration: 0, notes: [] });
  expect(next.notes).toEqual(state.notes);
});

it.each(['notes/created', 'notes/updated', 'notes/deleted'] as const)('keeps local notes when a delayed load follows %s', (type) => {
  const initial = { ...initialWorkspaceState, activePaperId: 'paper-a', loadRevision: 3, notes: [{ id: 'note-a', body: '初始', element_id: null, page_number: null }] };
  const action = type === 'notes/deleted'
    ? { type, paperId: 'paper-a', loadRevision: 3, noteId: 'note-a' }
    : { type, paperId: 'paper-a', loadRevision: 3, note: { id: 'note-a', body: '本地更新', element_id: null, page_number: null } };
  const afterMutation = workspaceReducer(initial, action);
  const afterLoad = workspaceReducer(afterMutation, { type: 'notes/loaded', paperId: 'paper-a', loadRevision: 3, mutationGeneration: 0, notes: [{ id: 'stale', body: '过期', element_id: null, page_number: null }] });
  expect(afterLoad.notes).toEqual(afterMutation.notes);
});

it('loads highlights independently of note mutation generation', () => {
  const state = { ...initialWorkspaceState, activePaperId: 'paper-a', loadRevision: 3, notesMutationGeneration: 4, highlightsMutationGeneration: 0 };
  const highlights = [{ id: 'highlight-a', color: 'yellow' as const, anchor: { id: 'anchor-a', quote: '原文', page_number: 1, element_id: null, rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 }] } }];
  const next = workspaceReducer(state, { type: 'highlights/loaded', paperId: 'paper-a', loadRevision: 3, mutationGeneration: 0, highlights });
  expect(next.highlights).toEqual(highlights);
});

it('adds a persisted highlight and clears only the temporary selection', () => {
  const selection = {
    draft: {
      quote: '论文片段', page_number: 2,
      rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.3 }],
    },
    toolbarRect: { left: 20, top: 30, width: 50, height: 10 } as DOMRect,
  };
  const state = { ...readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' }), selection };

  const next = workspaceReducer(state, {
    type: 'highlight/created',
    paperId: 'paper-a',
    loadRevision: 0,
    mutationGeneration: 1,
    highlight: {
      id: 'highlight-a', color: 'yellow',
      anchor: { id: 'anchor-a', element_id: null, ...selection.draft },
    },
  });

  expect(next.selection).toBeNull();
  expect(next.highlights.map(({ id }) => id)).toEqual(['highlight-a']);
});

it('ignores an Agent response that belongs to a paper that is no longer open', () => {
  const switchedWorkspace = workspaceReducer(
    readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' }),
    { type: 'paper/opened', paperId: 'paper-b', loadRevision: 0 },
  );

  const next = workspaceReducer(switchedWorkspace, {
    type: 'conversation/set',
    paperId: 'paper-a',
    loadRevision: 0,
    conversationId: 'chat-a',
    mode: 'paper_only',
    message: {
      conversation_id: 'chat-a',
      message_id: 'message-late',
      status: 'grounded',
      paper_answer: 'Late answer.',
      background_explanation: null,
      citations: [],
    },
  });

  expect(next).toEqual(switchedWorkspace);
});

it.each([
  ['graph build', {
    type: 'graph/loaded',
    paperId: 'paper-a',
    loadRevision: 1,
    graph: {
      nodes: [{
        id: 'stale-node',
        node_type: 'claim',
        name: 'Stale graph',
        summary: 'Built before retry.',
        stage: 'stage2',
        evidence_element_ids: [],
      }],
      edges: [],
    },
  }],
  ['Agent response', {
    type: 'conversation/set',
    paperId: 'paper-a',
    loadRevision: 1,
    conversationId: 'stale-chat',
    mode: 'paper_only',
    message: {
      conversation_id: 'stale-chat',
      message_id: 'stale-message',
      status: 'grounded',
      paper_answer: 'Stale answer.',
      background_explanation: null,
      citations: [],
    },
  }],
  ['note creation', {
    type: 'notes/created',
    paperId: 'paper-a',
    loadRevision: 1,
    note: {
      id: 'stale-note', body: 'Stale note.', element_id: null, page_number: null,
    },
  }],
  ['mutation failure', {
    type: 'request/failed',
    paperId: 'paper-a',
    loadRevision: 1,
    message: 'Stale failure.',
  }],
  ['note failure', {
    type: 'notes/failed',
    paperId: 'paper-a',
    loadRevision: 1,
    message: 'Stale note failure.',
  }],
])('ignores an obsolete same-paper %s after retry', (_name, action) => {
  const state: WorkspaceState = {
    ...readyWorkspace({ paperId: 'paper-a', conversationId: 'current-chat' }),
    loadRevision: 2,
  };

  const next = workspaceReducer(state, action as Parameters<typeof workspaceReducer>[1]);

  expect(next).toBe(state);
});

it('resets paper-specific state when the same paper opens with a new load revision', () => {
  const state: WorkspaceState = {
    ...readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' }),
    errorMessage: 'Paper document is not ready.',
    notesErrorMessage: 'Notes are unavailable.',
  };

  const next = workspaceReducer(state, {
    type: 'paper/opened',
    paperId: 'paper-a',
    loadRevision: 1,
  });

  expect(next).toMatchObject({
    activePaperId: 'paper-a',
    loadRevision: 1,
    document: null,
    graph: null,
    notes: [],
    activeSource: null,
    graphFocusNodeId: null,
    conversationId: null,
    conversationMode: null,
    messages: [],
    errorMessage: null,
    notesErrorMessage: null,
  });
});

it('keeps a notes-only error when the required workspace finishes loading', () => {
  const ready = readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' });
  const state: WorkspaceState = {
    ...initialWorkspaceState,
    activePaperId: 'paper-a',
    loadRevision: 2,
    notesErrorMessage: 'Notes are temporarily unavailable.',
  };

  const next = workspaceReducer(state, {
    type: 'workspace/loaded',
    paperId: 'paper-a',
    loadRevision: 2,
    document: ready.document!,
    graph: ready.graph!,
  });

  expect(next).toMatchObject({
    document: ready.document,
    graph: ready.graph,
    errorMessage: null,
    notesErrorMessage: 'Notes are temporarily unavailable.',
  });
});

it('ignores notes loaded for an obsolete retry generation', () => {
  const state: WorkspaceState = {
    ...initialWorkspaceState,
    activePaperId: 'paper-a',
    loadRevision: 3,
    notes: [{ id: 'current', body: 'Current note.', element_id: null, page_number: null }],
  };

  const next = workspaceReducer(state, {
    type: 'notes/loaded',
    paperId: 'paper-a',
    loadRevision: 2,
    notes: [{ id: 'stale', body: 'Stale note.', element_id: null, page_number: null }],
  });

  expect(next).toEqual(state);
});

it('clears the notes error when notes load for the current generation', () => {
  const state: WorkspaceState = {
    ...initialWorkspaceState,
    activePaperId: 'paper-a',
    loadRevision: 3,
    errorMessage: 'A separate request failed.',
    notesErrorMessage: 'Notes are temporarily unavailable.',
  };
  const notes = [{ id: 'current', body: 'Current note.', element_id: null, page_number: null }];

  const next = workspaceReducer(state, {
    type: 'notes/loaded',
    paperId: 'paper-a',
    loadRevision: 3,
    notes,
  });

  expect(next.notes).toEqual(notes);
  expect(next.notesErrorMessage).toBeNull();
  expect(next.errorMessage).toBe('A separate request failed.');
});

it('clears a notes-only error when a note is created', () => {
  const state: WorkspaceState = {
    ...readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' }),
    errorMessage: 'Graph service is unavailable.',
    notesErrorMessage: 'Unable to save this note.',
  };
  const note = { id: 'note-new', body: 'New note.', element_id: null, page_number: null };

  const next = workspaceReducer(state, {
    type: 'notes/created',
    paperId: 'paper-a',
    loadRevision: 0,
    note,
  });

  expect(next.notes).toContainEqual(note);
  expect(next.notesErrorMessage).toBeNull();
  expect(next.errorMessage).toBe('Graph service is unavailable.');
});

it('rejects an unlocated element as an active source target', () => {
  expect(toSourceTarget(unlocatedElement)).toBeNull();
});

it.each([
  ['a zero page', { page_number: 0 }],
  ['a negative page', { page_number: -1 }],
  ['a fractional page', { page_number: 1.5 }],
  ['a zero-width box', { bbox: { x0: 0.2, y0: 0.2, x1: 0.2, y1: 0.3 } }],
  ['a zero-height box', { bbox: { x0: 0.2, y0: 0.2, x1: 0.3, y1: 0.2 } }],
  ['a reversed horizontal box', { bbox: { x0: 0.8, y0: 0.2, x1: 0.3, y1: 0.4 } }],
  ['a reversed vertical box', { bbox: { x0: 0.2, y0: 0.8, x1: 0.4, y1: 0.3 } }],
  ['a box below the normalized range', { bbox: { x0: -0.1, y0: 0.2, x1: 0.4, y1: 0.3 } }],
  ['a box above the normalized range', { bbox: { x0: 0.2, y0: 0.3, x1: 1.1, y1: 0.4 } }],
  ['a box containing NaN', { bbox: { x0: Number.NaN, y0: 0.2, x1: 0.4, y1: 0.3 } }],
  ['a box containing Infinity', { bbox: { x0: 0.2, y0: 0.3, x1: 0.4, y1: Number.POSITIVE_INFINITY } }],
] satisfies Array<[string, Partial<DocumentElement>]>)('rejects located evidence with %s', (_name, overrides) => {
  expect(toSourceTarget({ ...locatedElement, ...overrides })).toBeNull();
});

it('preserves the API location when selecting a located element', () => {
  expect(toSourceTarget(locatedElement)).toEqual({
    id: 'element-a',
    kind: 'paragraph',
    pageNumber: 2,
    bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
  });
});
