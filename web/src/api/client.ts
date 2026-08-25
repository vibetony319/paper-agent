import type {
  AgentMessage,
  AnnotationBundle,
  AskAgentInput,
  Conversation,
  CreateHighlightInput,
  CreateNoteInput,
  GraphBuildInput,
  Highlight,
  ModelProfile,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
  Note,
  PaperDocument,
  PaperGraph,
  PaperSummary,
  UpdateNoteInput,
} from './types';

const REQUEST_FAILED_MESSAGE = '请求失败，请稍后重试。';

export function publicApiMessage(fallback = REQUEST_FAILED_MESSAGE): string {
  return fallback;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string | null;

  constructor(status: number, message: string, code: string | null = null, detail: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

export async function readApiError(response: Response): Promise<ApiError> {
  let detail: string | null = null;
  let code: string | null = null;
  try {
    const body: unknown = await response.json();
    if (typeof body === 'object' && body !== null) {
      if ('detail' in body && typeof body.detail === 'string') {
        detail = body.detail;
      }
      if ('code' in body && typeof body.code === 'string') {
        code = body.code;
      }
    }
  } catch {
    // Preserve the safe fallback for invalid or empty error bodies.
  }
  return new ApiError(response.status, publicApiMessage(), code, detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);

  if (!response.ok) {
    throw await readApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function jsonRequest(
  method: 'POST' | 'PATCH' | 'DELETE',
  body?: unknown,
  headers?: Record<string, string>,
): RequestInit {
  const allHeaders = {
    ...(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
    ...headers,
  };
  return {
    method,
    headers: Object.keys(allHeaders).length === 0 ? undefined : allHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

function revisionHeaders(revision: number): Record<string, string> {
  return { 'If-Match': String(revision) };
}

export const paperApi = {
  listPapers: () => request<PaperSummary[]>('/api/papers'),

  getPaper: (paperId: string) =>
    request<PaperSummary>(`/api/papers/${encodeURIComponent(paperId)}`),

  upload: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<PaperSummary>('/api/papers', { method: 'POST', body });
  },

  deletePaper: (paperId: string, confirmation: string) =>
    request<void>(
      `/api/papers/${encodeURIComponent(paperId)}`,
      jsonRequest('DELETE', { confirmation }),
    ),

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

  buildCoreGraph: (paperId: string, input: GraphBuildInput) =>
    request<PaperGraph>(
      `/api/papers/${encodeURIComponent(paperId)}/graph/core`,
      jsonRequest('POST', input),
    ),

  buildDeepGraph: (paperId: string, input: GraphBuildInput) =>
    request<PaperGraph>(
      `/api/papers/${encodeURIComponent(paperId)}/graph/deep`,
      jsonRequest('POST', input),
    ),

  getAnnotations: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<AnnotationBundle>(`/api/papers/${encodeURIComponent(paperId)}/annotations`, init),

  createHighlight: (paperId: string, input: CreateHighlightInput) =>
    request<Highlight>(
      `/api/papers/${encodeURIComponent(paperId)}/highlights`,
      jsonRequest('POST', input),
    ),

  deleteHighlight: (paperId: string, highlightId: string) =>
    request<void>(
      `/api/papers/${encodeURIComponent(paperId)}/highlights/${encodeURIComponent(highlightId)}`,
      jsonRequest('DELETE'),
    ),

  createNote: (paperId: string, input: CreateNoteInput, init?: Pick<RequestInit, 'signal'>) =>
    request<Note>(
      `/api/papers/${encodeURIComponent(paperId)}/notes`,
      { ...jsonRequest('POST', input), ...init },
    ),

  getNotes: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<Note[]>(`/api/papers/${encodeURIComponent(paperId)}/notes`, init),

  updateNote: (paperId: string, noteId: string, input: UpdateNoteInput, init?: Pick<RequestInit, 'signal'>) =>
    request<Note>(
      `/api/papers/${encodeURIComponent(paperId)}/notes/${encodeURIComponent(noteId)}`,
      { ...jsonRequest('PATCH', input), ...init },
    ),

  deleteNote: (paperId: string, noteId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<void>(
      `/api/papers/${encodeURIComponent(paperId)}/notes/${encodeURIComponent(noteId)}`,
      { ...jsonRequest('DELETE'), ...init },
    ),

  askAgent: (paperId: string, input: AskAgentInput) =>
    request<AgentMessage>(
      `/api/papers/${encodeURIComponent(paperId)}/agent/messages`,
      jsonRequest('POST', input),
    ),

  getConversation: (paperId: string, conversationId: string) =>
    request<Conversation>(
      `/api/papers/${encodeURIComponent(paperId)}/agent/conversations/${encodeURIComponent(conversationId)}`,
    ),

  listModelProfiles: () => request<ModelProfile[]>('/api/model-profiles'),

  createModelProfile: (input: ModelProfileCreateInput) =>
    request<ModelProfile>('/api/model-profiles', jsonRequest('POST', input)),

  updateModelProfile: (profileId: string, revision: number, input: ModelProfileUpdateInput) => {
    const { clear_api_key, ...fields } = input;
    const body = clear_api_key ? { ...fields, api_key: null } : fields;
    return request<ModelProfile>(
      `/api/model-profiles/${encodeURIComponent(profileId)}`,
      jsonRequest('PATCH', body, revisionHeaders(revision)),
    );
  },

  deleteModelProfile: (profileId: string, revision: number) =>
    request<void>(
      `/api/model-profiles/${encodeURIComponent(profileId)}`,
      jsonRequest('DELETE', undefined, revisionHeaders(revision)),
    ),

  setDefaultModelProfile: (profileId: string, revision: number) =>
    request<ModelProfile>(
      `/api/model-profiles/${encodeURIComponent(profileId)}/default`,
      jsonRequest('POST', undefined, revisionHeaders(revision)),
    ),

  testModelProfile: (profileId: string, revision: number) =>
    request<ModelProfile>(
      `/api/model-profiles/${encodeURIComponent(profileId)}/test`,
      jsonRequest('POST', undefined, revisionHeaders(revision)),
    ),
};
