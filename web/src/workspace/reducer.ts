import type {
  AgentMode,
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
  loadRevision: 0,
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
};

export type WorkspaceAction =
  | { type: 'paper/opened'; paperId: string | null; loadRevision: number }
  | {
    type: 'workspace/loaded';
    paperId: string;
    loadRevision: number;
    document: PaperDocument;
    graph: PaperGraph;
  }
  | { type: 'workspace/failed'; paperId: string; loadRevision: number; message: string }
  | { type: 'notes/loaded'; paperId: string; loadRevision: number; notes: Note[] }
  | { type: 'notes/failed'; paperId: string; loadRevision: number; message: string }
  | { type: 'graph/loaded'; paperId: string; loadRevision: number; graph: PaperGraph }
  | { type: 'source/selected'; source: SourceTarget | null }
  | { type: 'graph/focused'; nodeId: string | null }
  | {
    type: 'conversation/set';
    paperId: string;
    loadRevision: number;
    conversationId: string;
    mode: AgentMode;
    message: AgentMessage;
  }
  | { type: 'notes/created'; paperId: string; loadRevision: number; note: Note }
  | { type: 'request/failed'; paperId: string; loadRevision: number; message: string };

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

function isCurrentLoad(
  state: WorkspaceState,
  paperId: string,
  loadRevision: number,
): boolean {
  return isCurrentPaper(state, paperId) && state.loadRevision === loadRevision;
}

function resetForPaper(paperId: string | null, loadRevision: number): WorkspaceState {
  return {
    ...initialWorkspaceState,
    activePaperId: paperId,
    loadRevision,
  };
}

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case 'paper/opened':
      return state.activePaperId === action.paperId
        && state.loadRevision === action.loadRevision
        ? state
        : resetForPaper(action.paperId, action.loadRevision);
    case 'workspace/loaded':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? {
          ...state,
          document: action.document,
          graph: action.graph,
          errorMessage: null,
        }
        : state;
    case 'workspace/failed':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, errorMessage: action.message }
        : state;
    case 'notes/loaded':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, notes: action.notes, notesErrorMessage: null }
        : state;
    case 'notes/failed':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, notesErrorMessage: action.message }
        : state;
    case 'graph/loaded':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, graph: action.graph, errorMessage: null }
        : state;
    case 'source/selected':
      return { ...state, activeSource: action.source };
    case 'graph/focused':
      return { ...state, graphFocusNodeId: action.nodeId };
    case 'conversation/set':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? {
          ...state,
          conversationId: action.conversationId,
          conversationMode: action.mode,
          messages: [...state.messages, action.message],
          errorMessage: null,
        }
        : state;
    case 'notes/created':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? {
          ...state,
          notes: [...state.notes, action.note],
          notesErrorMessage: null,
        }
        : state;
    case 'request/failed':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, errorMessage: action.message }
        : state;
  }
}
