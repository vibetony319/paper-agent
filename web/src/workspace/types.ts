import type {
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
  document: PaperDocument | null;
  graph: PaperGraph | null;
  notes: Note[];
  activeSource: SourceTarget | null;
  graphFocusNodeId: string | null;
  conversationId: string | null;
  messages: AgentMessage[];
  errorMessage: string | null;
};
