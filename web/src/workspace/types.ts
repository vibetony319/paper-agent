import type {
  AgentMode,
  AgentMessage,
  BoundingBox,
  Note,
  PaperDocument,
  PaperGraph,
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
  activeSource: SourceTarget | null;
  graphFocusNodeId: string | null;
  conversationId: string | null;
  conversationMode: AgentMode | null;
  messages: AgentMessage[];
  errorMessage: string | null;
  notesErrorMessage: string | null;
};
