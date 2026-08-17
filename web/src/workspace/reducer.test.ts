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
    messages: [{
      conversation_id: conversationId,
      message_id: 'message-a',
      status: 'grounded',
      paper_answer: 'Old answer.',
      background_explanation: null,
      citations: [],
    }],
  };
}

it('clears paper-specific workspace data when a different paper opens', () => {
  const state = readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' });

  const next = workspaceReducer(state, {
    type: 'paper/opened',
    paperId: 'paper-b',
  });

  expect(next).toMatchObject({
    activePaperId: 'paper-b',
    document: null,
    graph: null,
    notes: [],
    activeSource: null,
    graphFocusNodeId: null,
    conversationId: null,
    messages: [],
  });
});

it('ignores an Agent response that belongs to a paper that is no longer open', () => {
  const switchedWorkspace = workspaceReducer(
    readyWorkspace({ paperId: 'paper-a', conversationId: 'chat-a' }),
    { type: 'paper/opened', paperId: 'paper-b' },
  );

  const next = workspaceReducer(switchedWorkspace, {
    type: 'conversation/set',
    paperId: 'paper-a',
    conversationId: 'chat-a',
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
