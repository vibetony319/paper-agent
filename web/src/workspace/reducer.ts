import type {
  AgentMessage,
  DocumentElement,
  Note,
  PaperDocument,
  PaperGraph,
} from '../api/types';
import type { SourceTarget, WorkspaceState } from './types';
import { hasValidSourceLocation } from './sourceTarget';

export const initialWorkspaceState: WorkspaceState = {
  activePaperId: null,
  document: null,
  graph: null,
  notes: [],
  activeSource: null,
  graphFocusNodeId: null,
  conversationId: null,
  messages: [],
  errorMessage: null,
};

export type WorkspaceAction =
  | { type: 'paper/opened'; paperId: string | null }
  | { type: 'document/loaded'; paperId: string; document: PaperDocument; notes: Note[] }
  | { type: 'graph/loaded'; paperId: string; graph: PaperGraph }
  | { type: 'source/selected'; source: SourceTarget | null }
  | { type: 'graph/focused'; nodeId: string | null }
  | { type: 'conversation/set'; paperId: string; conversationId: string; message: AgentMessage }
  | { type: 'notes/created'; paperId: string; note: Note }
  | { type: 'request/failed'; paperId: string; message: string };

export function toSourceTarget(element: DocumentElement): SourceTarget | null {
  if (!hasValidSourceLocation(element)) {
    return null;
  }

  return {
    id: element.id,
    kind: element.kind,
    pageNumber: element.page_number,
    bbox: element.bbox,
  };
}

function isCurrentPaper(state: WorkspaceState, paperId: string): boolean {
  return state.activePaperId === paperId;
}

function resetForPaper(paperId: string | null): WorkspaceState {
  return {
    ...initialWorkspaceState,
    activePaperId: paperId,
  };
}

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case 'paper/opened':
      return state.activePaperId === action.paperId
        ? state
        : resetForPaper(action.paperId);
    case 'document/loaded':
      return isCurrentPaper(state, action.paperId)
        ? { ...state, document: action.document, notes: action.notes, errorMessage: null }
        : state;
    case 'graph/loaded':
      return isCurrentPaper(state, action.paperId)
        ? { ...state, graph: action.graph, errorMessage: null }
        : state;
    case 'source/selected':
      return { ...state, activeSource: action.source };
    case 'graph/focused':
      return { ...state, graphFocusNodeId: action.nodeId };
    case 'conversation/set':
      return isCurrentPaper(state, action.paperId)
        ? {
          ...state,
          conversationId: action.conversationId,
          messages: [...state.messages, action.message],
          errorMessage: null,
        }
        : state;
    case 'notes/created':
      return isCurrentPaper(state, action.paperId)
        ? { ...state, notes: [...state.notes, action.note], errorMessage: null }
        : state;
    case 'request/failed':
      return isCurrentPaper(state, action.paperId)
        ? { ...state, errorMessage: action.message }
        : state;
  }
}
