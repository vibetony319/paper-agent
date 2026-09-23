import type {
  AnnotationBundle,
  ContextUsage,
  ConversationSummary,
  CreateHighlightInput,
  CreateNoteInput,
  Highlight,
  ModelProfile,
  ModelConnectionTestResult,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
  Note,
  PaperDocument,
  PaperSummary,
  StoredConversation,
  UpdateNoteInput,
  UpdateHighlightInput,
} from './types';

const REQUEST_FAILED_MESSAGE = '请求失败，请稍后重试。';

// Codes the API reports for stream and assist failures, mirroring
// ``_STREAM_ERROR_DETAILS`` in the backend so both sides stay readable.
const CODE_MESSAGES: Record<string, string> = {
  agent_failed: '模型未能完成有效回答，请重试或在模型设置中重新测试。',
  agent_not_ready: '论文尚未完成解析，暂时无法提问。',
  answer_too_long: '模型输出过长，请缩小问题范围后重试。',
  answer_unavailable: '回答已生成，但引用信息无法解析，请重试。',
  empty_answer: '助手回答为空，请重试。',
  invalid_selection: '选区无效。',
  model_unavailable: '模型档案不可用。',
  paper_busy: '论文正在删除。',
  request_conflict: '该请求已有不同的处理状态，请使用新的请求重试。',
  assist_failed: '模型未能完成选区请求。',
  assist_running: '该选区请求正在处理中。',
  assist_too_long: '模型输出过长。',
  empty_event_stream: '连接中断，请重试。',
  invalid_content_type: '服务返回了非流式响应，请重试。',
  invalid_event_data: '连接被中断，请重试。',
  unknown_event: '服务返回了未知事件，请重试。',
};

export function publicApiMessage(fallback = REQUEST_FAILED_MESSAGE): string {
  return fallback;
}

/**
 * Return the most specific public reason for a failed request.
 *
 * Only codes this app knows about are expanded: server-provided ``detail``
 * text is never echoed, so an upstream message cannot leak into the reader.
 */
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError) || error.code === null) {
    return publicApiMessage(fallback);
  }
  return CODE_MESSAGES[error.code] ?? publicApiMessage(fallback);
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

  listConversations: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<ConversationSummary[]>(`/api/papers/${encodeURIComponent(paperId)}/agent/conversations`, init),

  getConversation: (paperId: string, conversationId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<StoredConversation>(`/api/papers/${encodeURIComponent(paperId)}/agent/conversations/${encodeURIComponent(conversationId)}`, init),

  getContextUsage: (
    paperId: string,
    conversationId: string,
    modelProfileId: string,
    init?: Pick<RequestInit, 'signal'>,
  ) =>
    request<ContextUsage>(
      `/api/papers/${encodeURIComponent(paperId)}/agent/conversations`
      + `/${encodeURIComponent(conversationId)}/context-usage`
      + `?model_profile_id=${encodeURIComponent(modelProfileId)}`,
      init,
    ),

  getSourceUrl: (paperId: string) =>
    `/api/papers/${encodeURIComponent(paperId)}/source`,

  getAnnotations: (paperId: string, init?: Pick<RequestInit, 'signal'>) =>
    request<AnnotationBundle>(`/api/papers/${encodeURIComponent(paperId)}/annotations`, init),

  createHighlight: (paperId: string, input: CreateHighlightInput) =>
    request<Highlight>(
      `/api/papers/${encodeURIComponent(paperId)}/highlights`,
      jsonRequest('POST', input),
    ),

  updateHighlight: (
    paperId: string,
    highlightId: string,
    input: UpdateHighlightInput,
    init?: Pick<RequestInit, 'signal'>,
  ) =>
    request<Highlight>(
      `/api/papers/${encodeURIComponent(paperId)}/highlights/${encodeURIComponent(highlightId)}`,
      { ...jsonRequest('PATCH', input), ...init },
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
    request<ModelConnectionTestResult>(
      `/api/model-profiles/${encodeURIComponent(profileId)}/test`,
      jsonRequest('POST', undefined, revisionHeaders(revision)),
    ),
};
