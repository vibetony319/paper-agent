import type {
  AgentMode,
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
  selection: { draft: TextAnchorDraft; toolbarRect: DOMRect } | null;
  activeSource: SourceTarget | null;
  graphFocusNodeId: string | null;
  conversationId: string | null;
  conversationMode: AgentMode | null;
  messages: AgentMessage[];
  errorMessage: string | null;
  notesErrorMessage: string | null;
};
