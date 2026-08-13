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

it('rejects an unlocated element as an active source target', () => {
  expect(toSourceTarget(unlocatedElement)).toBeNull();
});

it('preserves the API location when selecting a located element', () => {
  expect(toSourceTarget(locatedElement)).toEqual({
    id: 'element-a',
    kind: 'paragraph',
    pageNumber: 2,
    bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
  });
});
