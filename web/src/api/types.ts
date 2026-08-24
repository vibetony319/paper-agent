export type ProcessingStatus = 'queued' | 'running' | 'completed' | 'partial' | 'failed';
export type AgentMode = 'paper_only' | 'external_knowledge';
export type AgentMessageRole = 'user' | 'assistant';
export type NoteType = 'manual' | 'explanation' | 'translation';

export interface ModelSnapshot {
  profile_id: string;
  display_name: string;
  base_url: string;
  model_name: string;
  revision: number;
}

export interface ModelProfile {
  id: string;
  display_name: string;
  base_url: string;
  model_name: string;
  enabled: boolean;
  is_default: boolean;
  revision: number;
  has_api_key: boolean;
  api_key_mask: string | null;
  capabilities: {
    basic_chat: boolean;
    structured_output: boolean;
    tool_calling: boolean;
    checked_at: string | null;
  };
  read_only: boolean;
}

export type ModelProfileCreateInput = Pick<
  ModelProfile,
  'display_name' | 'base_url' | 'model_name' | 'enabled' | 'is_default'
> & { api_key?: string };

export type ModelProfileUpdateInput = Partial<ModelProfileCreateInput> & {
  clear_api_key?: boolean;
};

export interface TextAnchorRect {
  order: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface TextAnchor {
  id: string;
  quote: string;
  page_number: number;
  element_id: string | null;
  rects: TextAnchorRect[];
}

export interface TextAnchorDraft {
  quote: string;
  page_number: number;
  rects: TextAnchorRect[];
  element_id?: string;
}

export interface Highlight {
  id: string;
  color: 'yellow';
  anchor: TextAnchor;
}

export interface CreateHighlightInput extends TextAnchorDraft {
  color?: 'yellow';
  request_id: string;
}

export interface AnnotationBundle {
  highlights: Highlight[];
  notes: Note[];
  anchors?: TextAnchor[];
}

export type SelectionAssistAction = 'explain' | 'translate';

export interface SelectionAssistInput extends TextAnchorDraft {
  action: SelectionAssistAction;
  model_profile_id: string;
  request_id: string;
}

export type SelectionAssistEvent =
  | { event: 'started'; data: { request_id: string } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'completed'; data: { note: Note } }
  | { event: 'error'; data: { code: string; detail: string } };

export interface NoteReference {
  note_id: string;
  note_type: NoteType | null;
  page_number: number | null;
  available: boolean;
}

export interface PaperSummary {
  id: string;
  original_filename: string;
  status: ProcessingStatus;
  stage0_status: ProcessingStatus;
  stage1_status: ProcessingStatus;
  stage2_status: ProcessingStatus | null;
  stage3_status: ProcessingStatus | null;
  stage2_model?: ModelSnapshot | null;
  stage3_model?: ModelSnapshot | null;
  error: string | null;
}

export interface Paper {
  id: string;
  original_filename: string;
  status: ProcessingStatus;
}

export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Page {
  id: string;
  number: number;
  width: number;
  height: number;
}

export interface Section {
  id: string;
  title: string;
  page_number: number | null;
  order: number;
}

export interface DocumentElement {
  id: string;
  kind: string;
  text: string;
  page_number: number | null;
  bbox: BoundingBox | null;
  section_id: string | null;
  location_status: 'located' | 'unlocated';
  order: number;
}

export interface Note {
  id: string;
  body: string;
  element_id: string | null;
  page_number: number | null;
  note_type?: NoteType;
  anchor_ids?: string[];
  model?: ModelSnapshot | null;
  ai_generated?: boolean;
  user_edited?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PaperDocument {
  paper: Paper;
  pages: Page[];
  sections: Section[];
  elements: DocumentElement[];
  notes: Note[];
}

export interface GraphNode {
  id: string;
  node_type: string;
  name: string;
  summary: string;
  stage: 'stage2' | 'stage3';
  evidence_element_ids: string[];
}

export interface GraphEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  relation_type: string;
  stage: 'stage2' | 'stage3';
  evidence_element_ids: string[];
}

export interface PaperGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Citation {
  id: string;
  kind: string;
  page_number: number;
  bbox: BoundingBox;
}

export interface AgentMessage {
  conversation_id: string;
  message_id: string;
  status: 'grounded' | 'insufficient_evidence';
  paper_answer: string;
  background_explanation: string | null;
  citations: Citation[];
  model?: ModelSnapshot | null;
  note_references?: NoteReference[];
}

export interface ConversationMessage {
  id: string;
  role: AgentMessageRole;
  content: string;
  citations: Citation[];
}

export interface Conversation {
  id: string;
  paper_id: string;
  mode: AgentMode;
  messages: ConversationMessage[];
}

export interface CreateNoteInput {
  body: string;
  element_id?: string;
  page_number?: number;
  anchor?: TextAnchorDraft;
  request_id?: string;
}

export interface UpdateNoteInput {
  body: string;
  expected_updated_at?: string | null;
}

export interface AskAgentInput {
  content: string;
  mode: AgentMode;
  conversation_id?: string;
  model_profile_id: string;
  request_id: string;
  selection?: TextAnchorDraft;
}

export interface GraphBuildInput {
  model_profile_id: string;
  request_id: string;
}
