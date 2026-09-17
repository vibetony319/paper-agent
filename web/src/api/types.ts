export type ProcessingStatus = 'queued' | 'running' | 'completed' | 'partial' | 'failed';
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
  context_length: number | null;
  max_output_tokens: number | null;
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
> & {
  api_key?: string;
  context_length?: number | null;
  max_output_tokens?: number | null;
};

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

export type HighlightColor = 'yellow' | 'green' | 'blue' | 'pink';

export interface Highlight {
  id: string;
  color: HighlightColor;
  anchor: TextAnchor;
}

export interface CreateHighlightInput extends TextAnchorDraft {
  color?: HighlightColor;
  request_id: string;
}

export interface UpdateHighlightInput {
  color: HighlightColor;
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
  /** Navigation nesting depth; 1 is top level. Older payloads omit it. */
  level?: number;
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
  conversation_id?: string;
  model_profile_id: string;
  request_id: string;
  selection?: TextAnchorDraft;
}

/** Estimated occupancy of the conversation's next model request. */
export interface ContextUsage {
  used_tokens: number;
  context_length: number | null;
  effective_limit: number | null;
  compaction_threshold: number | null;
  percent: number | null;
}

/** One execution step of a streamed agent answer. */
export type AgentStreamStep =
  | { kind: 'notes'; count: number }
  | { kind: 'round'; round: number }
  | { kind: 'reasoning'; round: number; text: string }
  | { kind: 'tool_call'; round: number; tool_name: string; arguments?: Record<string, unknown> }
  | {
    kind: 'tool_result';
    round: number;
    tool_name: string;
    evidence_count?: number;
    error?: string;
  }
  | { kind: 'compaction' }
  | { kind: 'final_answer' };

export type AgentStreamEvent =
  | { event: 'started'; data: { request_id: string } }
  | { event: 'step'; data: AgentStreamStep }
  | { event: 'delta'; data: { text: string } }
  | { event: 'completed'; data: { message: AgentMessage; context_usage?: ContextUsage | null } }
  | { event: 'error'; data: { code: string; detail: string } };
