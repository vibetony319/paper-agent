import type {
  AgentMessage,
  AskAgentInput,
  Conversation,
  CreateNoteInput,
  Note,
  PaperDocument,
  PaperGraph,
  PaperSummary,
} from './types';

const REQUEST_FAILED_MESSAGE = 'Request failed.';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);

  if (!response.ok) {
    let message = REQUEST_FAILED_MESSAGE;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === 'object'
        && body !== null
        && 'detail' in body
        && typeof body.detail === 'string'
      ) {
        message = body.detail;
      }
    } catch {
      // Preserve the safe fallback for invalid or empty error bodies.
    }
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<T>;
}

function jsonRequest(method: 'POST', body?: unknown): RequestInit {
  return {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export const paperApi = {
  listPapers: () => request<PaperSummary[]>('/api/papers'),

  upload: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<PaperSummary>('/api/papers', { method: 'POST', body });
  },

  getDocument: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<PaperDocument>(`/api/papers/${encodeURIComponent(paperId)}/document`, init),

  getSourceUrl: (paperId: string) =>
    `/api/papers/${encodeURIComponent(paperId)}/source`,

  getGraph: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<PaperGraph>(`/api/papers/${encodeURIComponent(paperId)}/graph`, init),

  getGraphSubgraph: (paperId: string, nodeId: string, depth = 1) => {
    const params = new URLSearchParams({ node_id: nodeId, depth: String(depth) });
    return request<PaperGraph>(
      `/api/papers/${encodeURIComponent(paperId)}/graph/subgraph?${params}`,
    );
  },

  buildCoreGraph: (paperId: string) =>
    request<PaperGraph>(
      `/api/papers/${encodeURIComponent(paperId)}/graph/core`,
      jsonRequest('POST'),
    ),

  buildDeepGraph: (paperId: string) =>
    request<PaperGraph>(
      `/api/papers/${encodeURIComponent(paperId)}/graph/deep`,
      jsonRequest('POST'),
    ),

  createNote: (paperId: string, input: CreateNoteInput) =>
    request<Note>(
      `/api/papers/${encodeURIComponent(paperId)}/notes`,
      jsonRequest('POST', input),
    ),

  getNotes: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<Note[]>(`/api/papers/${encodeURIComponent(paperId)}/notes`, init),

  askAgent: (paperId: string, input: AskAgentInput) =>
    request<AgentMessage>(
      `/api/papers/${encodeURIComponent(paperId)}/agent/messages`,
      jsonRequest('POST', input),
    ),

  getConversation: (paperId: string, conversationId: string) =>
    request<Conversation>(
      `/api/papers/${encodeURIComponent(paperId)}/agent/conversations/${encodeURIComponent(conversationId)}`,
    ),
};
