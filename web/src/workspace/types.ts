import type {
  AgentMessage,
  BoundingBox,
  Highlight,
  Note,
  TextAnchor,
  PaperDocument,
  PaperGraph,
  TextAnchorDraft,
} from '../api/types';

export type SourceTarget = {
  id: string;
  kind: string;
  pageNumber: number;
  bbox: BoundingBox;
};

export type WorkspaceState = {
  activePaperId: string | null;
  loadRevision: number;
  document: PaperDocument | null;
  graph: PaperGraph | null;
  notes: Note[];
  highlights: Highlight[];
  anchors: TextAnchor[];
  highlightsMutationGeneration: number;
  notesMutationGeneration: number;
  anchorsMutationGeneration: number;
  selection: { draft: TextAnchorDraft; toolbarRect: DOMRect } | null;
  activeSource: SourceTarget | null;
  graphFocusNodeId: string | null;
  conversationId: string | null;
  messages: AgentMessage[];
  exchanges: AgentExchange[];
  errorMessage: string | null;
  notesErrorMessage: string | null;
};

export type AgentExchange = {
  question: string;
  message: AgentMessage;
};
