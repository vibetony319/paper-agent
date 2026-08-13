export type ProcessingStatus = 'queued' | 'running' | 'completed' | 'partial' | 'failed';
export type AgentMode = 'paper_only' | 'external_knowledge';
export type AgentMessageRole = 'user' | 'assistant';

export interface PaperSummary {
  id: string;
  original_filename: string;
  status: ProcessingStatus;
  stage0_status: ProcessingStatus;
  stage1_status: ProcessingStatus;
  stage2_status: ProcessingStatus | null;
  stage3_status: ProcessingStatus | null;
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
  stage: 'core' | 'deep';
  evidence_element_ids: string[];
}

export interface GraphEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  relation_type: string;
  stage: 'core' | 'deep';
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
}

export interface AskAgentInput {
  content: string;
  mode: AgentMode;
  conversation_id?: string;
}
