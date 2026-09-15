import type {
  AgentMessage,
  BoundingBox,
  Highlight,
  Note,
  TextAnchor,
  PaperDocument,
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
  notes: Note[];
  highlights: Highlight[];
  anchors: TextAnchor[];
  highlightsMutationGeneration: number;
  notesMutationGeneration: number;
  anchorsMutationGeneration: number;
  selection: { draft: TextAnchorDraft; toolbarRect: DOMRect } | null;
  activeSource: SourceTarget | null;
  conversationId: string | null;
  messages: AgentMessage[];
  exchanges: AgentExchange[];
  streaming: AgentStream | null;
  errorMessage: string | null;
  notesErrorMessage: string | null;
};

export type AgentExchange = {
  question: string;
  message: AgentMessage;
};

export type AgentStream = {
  question: string;
  text: string;
  /** True once the request failed, so the partial text is kept but not final. */
  interrupted: boolean;
};
