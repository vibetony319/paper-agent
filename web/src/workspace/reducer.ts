import type {
  AgentMode,
  AgentMessage,
  DocumentElement,
  Highlight,
  Note,
  TextAnchor,
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
  highlights: [],
  anchors: [],
  highlightsMutationGeneration: 0,
  selection: null,
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
  | {
    type: 'highlights/loaded';
    paperId: string;
    loadRevision: number;
    mutationGeneration: number;
    highlights: Highlight[];
  }
  | { type: 'anchors/loaded'; paperId: string; loadRevision: number; anchors: TextAnchor[] }
  | { type: 'anchor/created'; paperId: string; loadRevision: number; anchor: TextAnchor }
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
  | { type: 'notes/updated'; paperId: string; loadRevision: number; note: Note }
  | { type: 'notes/deleted'; paperId: string; loadRevision: number; noteId: string }
  | { type: 'selection/set'; draft: import('../api/types').TextAnchorDraft; toolbarRect: DOMRect }
  | { type: 'selection/clear' }
  | {
    type: 'highlight/created';
    paperId: string;
    loadRevision: number;
    mutationGeneration: number;
    highlight: Highlight;
  }
  | {
    type: 'highlight/deleted';
    paperId: string;
    loadRevision: number;
    mutationGeneration: number;
    highlightId: string;
  }
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
    case 'highlights/loaded':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        && state.highlightsMutationGeneration === action.mutationGeneration
        ? { ...state, highlights: action.highlights }
        : state;
    case 'anchors/loaded':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, anchors: action.anchors }
        : state;
    case 'anchor/created':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, anchors: [...state.anchors.filter(({ id }) => id !== action.anchor.id), action.anchor] }
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
    case 'notes/updated':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, notes: state.notes.map((note) => note.id === action.note.id ? action.note : note) }
        : state;
    case 'notes/deleted':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, notes: state.notes.filter((note) => note.id !== action.noteId) }
        : state;
    case 'selection/set':
      return { ...state, selection: { draft: action.draft, toolbarRect: action.toolbarRect } };
    case 'selection/clear':
      return state.selection === null ? state : { ...state, selection: null };
    case 'highlight/created':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? {
          ...state,
          highlights: [...state.highlights, action.highlight],
          highlightsMutationGeneration: action.mutationGeneration,
          selection: null,
          errorMessage: null,
        }
        : state;
    case 'highlight/deleted':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? {
          ...state,
          highlights: state.highlights.filter(({ id }) => id !== action.highlightId),
          highlightsMutationGeneration: action.mutationGeneration,
        }
        : state;
    case 'request/failed':
      return isCurrentLoad(state, action.paperId, action.loadRevision)
        ? { ...state, errorMessage: action.message }
        : state;
  }
}
