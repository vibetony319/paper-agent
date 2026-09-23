import { useCallback, useEffect, useReducer, useRef } from 'react';

import { ApiError, apiErrorMessage, paperApi } from '../api/client';
import { newRequestId } from '../api/ids';
import { streamAgentMessage, streamSelectionAssist } from '../api/sse';
import type {
  AgentMessage,
  Citation,
  ContextUsage,
  HighlightColor,
  SelectionAssistAction,
  TextAnchorDraft,
  StoredConversation,
} from '../api/types';
import {
  initialWorkspaceState,
  toSourceTarget,
  workspaceReducer,
} from './reducer';
import type { AgentExchange } from './types';

function exchangesFromConversation(conversation: StoredConversation): AgentExchange[] {
  const exchanges: AgentExchange[] = [];
  let question = '';
  for (const stored of conversation.messages) {
    if (stored.role === 'user') {
      question = stored.content;
    } else {
      exchanges.push({
        question,
        steps: [],
        message: {
          conversation_id: conversation.id,
          message_id: stored.id,
          status: stored.citations.length > 0 ? 'grounded' : 'insufficient_evidence',
          paper_answer: stored.content,
          background_explanation: stored.background_explanation ?? null,
          citations: stored.citations,
          model: stored.model,
          note_references: stored.note_references,
        },
      });
      question = '';
    }
  }
  return exchanges;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function publicWorkspaceError(error: unknown, fallback: string): string {
  return apiErrorMessage(error, fallback);
}

function selectionAssistFailureMessage(action: SelectionAssistAction): string {
  return action === 'translate' ? '翻译请求失败，请重试。' : '解释请求失败，请重试。';
}

export function usePaperWorkspace(
  activePaperId: string | null,
  loadRevision = 0,
  modelProfileId: string | null = null,
) {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState);
  // Keep reducer guards unique even when callers reset their retry trigger.
  const nextLoadGeneration = useRef(0);
  const highlightsMutationGeneration = useRef(0);
  const notesMutationGeneration = useRef(0);
  const anchorsMutationGeneration = useRef(0);
  const workspaceLifetimeController = useRef(new AbortController());
  const agentStreamController = useRef<AbortController | null>(null);
  const historyController = useRef<AbortController | null>(null);
  const conversationChoiceGeneration = useRef(0);

  const reportApiError = useCallback((
    paperId: string,
    loadRevision: number,
    error: unknown,
    fallback = '论文阅读工作区暂时无法加载。',
  ) => {
    if (isAbortError(error)) {
      return;
    }
    dispatch({
      type: 'request/failed',
      paperId,
      loadRevision,
      message: publicWorkspaceError(error, fallback),
    });
  }, []);

  useEffect(() => {
    const loadGeneration = nextLoadGeneration.current++;
    const controller = new AbortController();
    highlightsMutationGeneration.current = 0;
    notesMutationGeneration.current = 0;
    anchorsMutationGeneration.current = 0;
    workspaceLifetimeController.current.abort();
    historyController.current?.abort();
    conversationChoiceGeneration.current += 1;
    const lifetimeController = new AbortController();
    workspaceLifetimeController.current = lifetimeController;
    const annotationsMutationGeneration = highlightsMutationGeneration.current;
    dispatch({ type: 'paper/opened', paperId: activePaperId, loadRevision: loadGeneration });

    if (activePaperId === null) {
      return () => controller.abort();
    }

    void paperApi.getDocument(activePaperId, { signal: controller.signal })
      .then((document) => {
        if (controller.signal.aborted) {
          return;
        }
        dispatch({
          type: 'workspace/loaded',
          paperId: activePaperId,
          loadRevision: loadGeneration,
          document,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) {
          return;
        }
        dispatch({
          type: 'workspace/failed',
          paperId: activePaperId,
          loadRevision: loadGeneration,
          message: publicWorkspaceError(error, '论文阅读工作区暂时无法加载。'),
        });
      });

    const notesLoadMutationGeneration = notesMutationGeneration.current;
    void paperApi.getNotes(activePaperId, { signal: controller.signal })
      .then((notes) => {
        if (controller.signal.aborted) {
          return;
        }
        dispatch({
          type: 'notes/loaded', paperId: activePaperId, loadRevision: loadGeneration, mutationGeneration: notesLoadMutationGeneration, notes,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) {
          return;
        }
        dispatch({
          type: 'notes/failed',
          paperId: activePaperId,
          loadRevision: loadGeneration,
          message: publicWorkspaceError(error, '论文笔记暂时无法加载。'),
        });
      });

    const choiceGeneration = conversationChoiceGeneration.current;
    void paperApi.listConversations(activePaperId, { signal: controller.signal })
      .then(async (conversations) => {
        if (controller.signal.aborted) return;
        dispatch({ type: 'conversation/list-loaded', paperId: activePaperId, loadRevision: loadGeneration, conversations });
        const latest = conversations[0];
        if (latest === undefined || conversationChoiceGeneration.current !== choiceGeneration) return;
        dispatch({ type: 'conversation/select', paperId: activePaperId, loadRevision: loadGeneration, conversationId: latest.id });
        try {
          const history = await paperApi.getConversation(activePaperId, latest.id, { signal: controller.signal });
          if (!controller.signal.aborted && conversationChoiceGeneration.current === choiceGeneration) {
            dispatch({ type: 'conversation/history-loaded', paperId: activePaperId, loadRevision: loadGeneration, conversationId: latest.id, exchanges: exchangesFromConversation(history) });
          }
        } catch (error) {
          if (!controller.signal.aborted && !isAbortError(error)) dispatch({ type: 'conversation/history-failed', paperId: activePaperId, loadRevision: loadGeneration, conversationId: latest.id, message: publicWorkspaceError(error, '对话记录暂时无法加载。') });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isAbortError(error)) reportApiError(activePaperId, loadGeneration, error, '对话记录暂时无法加载。');
      });

    const anchorsLoadMutationGeneration = anchorsMutationGeneration.current;
    void paperApi.getAnnotations(activePaperId, { signal: controller.signal })
      .then(({ highlights, anchors = [] }) => {
        if (controller.signal.aborted) return;
        dispatch({
          type: 'highlights/loaded', paperId: activePaperId, loadRevision: loadGeneration, highlights,
          mutationGeneration: annotationsMutationGeneration,
        });
        dispatch({ type: 'anchors/loaded', paperId: activePaperId, loadRevision: loadGeneration, mutationGeneration: anchorsLoadMutationGeneration, anchors });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        dispatch({
          type: 'request/failed',
          paperId: activePaperId,
          loadRevision: loadGeneration,
          message: publicWorkspaceError(error, '无法加载论文高亮。'),
        });
      });

    return () => {
      controller.abort();
      lifetimeController.abort();
    };
  }, [activePaperId, loadRevision, reportApiError]);

  const selectConversation = useCallback(async (conversationId: string | null) => {
    if (state.activePaperId === null || (state.streaming !== null && !state.streaming.interrupted)) return;
    conversationChoiceGeneration.current += 1;
    historyController.current?.abort();
    const paperId = state.activePaperId;
    const revision = state.loadRevision;
    dispatch({ type: 'conversation/select', paperId, loadRevision: revision, conversationId });
    if (conversationId === null) return;
    const controller = new AbortController();
    historyController.current = controller;
    try {
      const history = await paperApi.getConversation(paperId, conversationId, { signal: controller.signal });
      if (!controller.signal.aborted) dispatch({ type: 'conversation/history-loaded', paperId, loadRevision: revision, conversationId, exchanges: exchangesFromConversation(history) });
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error)) dispatch({ type: 'conversation/history-failed', paperId, loadRevision: revision, conversationId, message: publicWorkspaceError(error, '对话记录暂时无法加载。') });
    }
  }, [state.activePaperId, state.loadRevision, state.streaming]);

  // The usage readout is auxiliary: a failed refresh leaves the last value.
  useEffect(() => {
    if (activePaperId === null || modelProfileId === null || state.conversationId === null) {
      return;
    }
    const controller = new AbortController();
    void paperApi.getContextUsage(
      activePaperId,
      state.conversationId,
      modelProfileId,
      { signal: controller.signal },
    )
      .then((usage) => {
        if (controller.signal.aborted) return;
        dispatch({
          type: 'context/usage-set',
          paperId: activePaperId,
          loadRevision: state.loadRevision,
          usage,
        });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [activePaperId, modelProfileId, state.conversationId, state.loadRevision]);

  const askAgent = useCallback(async (
    content: string,
    modelProfileId: string,
    selection?: TextAnchorDraft,
  ) => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    conversationChoiceGeneration.current += 1;
    historyController.current?.abort();
    const requestLoadRevision = state.loadRevision;
    const conversationId = state.conversationId;
    const controller = new AbortController();
    agentStreamController.current?.abort();
    agentStreamController.current = controller;
    dispatch({
      type: 'conversation/stream-started',
      paperId,
      loadRevision: requestLoadRevision,
      question: content,
    });
    try {
      let message: AgentMessage | null = null;
      let contextUsage: ContextUsage | null = null;
      for await (const event of streamAgentMessage(paperId, {
        content,
        conversation_id: conversationId ?? undefined,
        model_profile_id: modelProfileId,
        request_id: newRequestId(),
        ...(selection === undefined ? {} : { selection }),
      }, controller.signal)) {
        if (event.event === 'delta') {
          dispatch({
            type: 'conversation/stream-delta',
            paperId,
            loadRevision: requestLoadRevision,
            text: event.data.text,
          });
        } else if (event.event === 'step') {
          dispatch({
            type: 'conversation/stream-step',
            paperId,
            loadRevision: requestLoadRevision,
            step: event.data,
          });
        } else if (event.event === 'completed') {
          message = event.data.message;
          contextUsage = event.data.context_usage ?? null;
        } else if (event.event === 'error') {
          throw new ApiError(0, '助手回答失败。', event.data.code, event.data.detail);
        }
      }
      if (message === null) {
        throw new ApiError(0, '助手回答为空。', 'empty_answer');
      }
      dispatch({
        type: 'conversation/set',
        paperId,
        loadRevision: requestLoadRevision,
        conversationId: message.conversation_id,
        question: content,
        message,
      });
      dispatch({
        type: 'context/usage-set',
        paperId,
        loadRevision: requestLoadRevision,
        usage: contextUsage,
      });
      return message;
    } catch (error) {
      if (agentStreamController.current !== controller) {
        // A newer question superseded this stream; its own result stands.
        return null;
      }
      dispatch({
        type: 'conversation/stream-failed',
        paperId,
        loadRevision: requestLoadRevision,
      });
      reportApiError(
        paperId,
        requestLoadRevision,
        error,
        '暂时无法获取助手回答。',
      );
      return null;
    } finally {
      if (agentStreamController.current === controller) {
        agentStreamController.current = null;
      }
    }
  }, [
    reportApiError,
    state.activePaperId,
    state.conversationId,
    state.loadRevision,
  ]);

  const saveNote = useCallback(async (
    body: string,
    elementId?: string,
    pageNumber?: number,
    anchor?: TextAnchorDraft,
  ) => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    const requestLoadRevision = state.loadRevision;
    try {
      const note = await paperApi.createNote(paperId, {
        body,
        element_id: elementId,
        page_number: pageNumber,
        ...(anchor === undefined ? {} : { anchor, request_id: newRequestId() }),
      }, { signal: workspaceLifetimeController.current.signal });
      const noteMutationGeneration = notesMutationGeneration.current + 1;
      notesMutationGeneration.current = noteMutationGeneration;
      dispatch({
        type: 'notes/created', paperId, loadRevision: requestLoadRevision, mutationGeneration: noteMutationGeneration, note,
      });
      if (anchor !== undefined && note.anchor_ids?.[0] !== undefined) {
        const anchorMutationGeneration = anchorsMutationGeneration.current + 1;
        anchorsMutationGeneration.current = anchorMutationGeneration;
        dispatch({
          type: 'anchor/created', paperId, loadRevision: requestLoadRevision,
          mutationGeneration: anchorMutationGeneration,
          anchor: { ...anchor, id: note.anchor_ids[0], element_id: anchor.element_id ?? null },
        });
      }
      return note;
    } catch (error) {
      if (!isAbortError(error)) {
        dispatch({
          type: 'notes/failed',
          paperId,
          loadRevision: requestLoadRevision,
          message: publicWorkspaceError(error, '笔记暂时无法保存。'),
        });
      }
      return null;
    }
  }, [state.activePaperId, state.loadRevision]);

  const runSelectionAssist = useCallback(async (
    action: SelectionAssistAction,
    draft: TextAnchorDraft,
    modelProfileId: string,
    requestId: string,
    signal: AbortSignal,
    onDelta?: (text: string) => void,
  ) => {
    if (state.activePaperId === null) return { status: 'failed' as const, text: '当前论文不可用。' };
    const paperId = state.activePaperId;
    const requestLoadRevision = state.loadRevision;
    let text = '';
    try {
      for await (const event of streamSelectionAssist(paperId, {
        ...draft, action, model_profile_id: modelProfileId, request_id: requestId,
      }, signal)) {
        if (event.event === 'delta') { text += event.data.text; onDelta?.(event.data.text); }
        if (event.event === 'completed') {
          const noteMutationGeneration = notesMutationGeneration.current + 1;
          notesMutationGeneration.current = noteMutationGeneration;
          dispatch({ type: 'notes/created', paperId, loadRevision: requestLoadRevision, mutationGeneration: noteMutationGeneration, note: event.data.note });
          if (event.data.note.anchor_ids?.[0] !== undefined) {
            const anchorMutationGeneration = anchorsMutationGeneration.current + 1;
            anchorsMutationGeneration.current = anchorMutationGeneration;
            dispatch({ type: 'anchor/created', paperId, loadRevision: requestLoadRevision, mutationGeneration: anchorMutationGeneration, anchor: { ...draft, id: event.data.note.anchor_ids[0], element_id: draft.element_id ?? null } });
          }
          return { status: 'completed' as const, text: event.data.note.body };
        }
        if (event.event === 'error') {
          return {
            status: 'failed' as const,
            text,
            message: apiErrorMessage(
              new ApiError(0, '', event.data.code, event.data.detail),
              selectionAssistFailureMessage(action),
            ),
          };
        }
      }
      return {
        status: 'failed' as const,
        text,
        message: action === 'translate' ? '翻译请求未完成。' : '解释请求未完成。',
      };
    } catch (error) {
      return isAbortError(error) || signal.aborted
        ? { status: 'cancelled' as const, text }
        : { status: 'failed' as const, text, message: publicWorkspaceError(error, selectionAssistFailureMessage(action)) };
    }
  }, [state.activePaperId, state.loadRevision]);

  const selectCitation = useCallback((citation: Citation) => {
    const { x0, y0, x1, y1 } = citation.bbox;
    const valid = Number.isInteger(citation.page_number) && citation.page_number > 0
      && [x0, y0, x1, y1].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)
      && x0 < x1 && y0 < y1;
    dispatch({ type: 'source/selected', source: valid ? {
      id: citation.id, kind: citation.kind, pageNumber: citation.page_number, bbox: citation.bbox,
    } : null });
  }, []);

  const selectElementSource = useCallback((elementId: string) => {
    const element = state.document?.elements.find(({ id }) => id === elementId);
    dispatch({ type: 'source/selected', source: element === undefined ? null : toSourceTarget(element) });
  }, [state.document]);

  const selectAnchorSource = useCallback((source: import('./types').SourceTarget) => {
    dispatch({ type: 'source/selected', source });
  }, []);

  const updateNote = useCallback(async (noteId: string, body: string, expectedUpdatedAt?: string | null) => {
    if (state.activePaperId === null) return null;
    const paperId = state.activePaperId; const revision = state.loadRevision;
    try {
      const note = await paperApi.updateNote(paperId, noteId, { body, expected_updated_at: expectedUpdatedAt }, { signal: workspaceLifetimeController.current.signal });
      const mutationGeneration = notesMutationGeneration.current + 1; notesMutationGeneration.current = mutationGeneration;
      dispatch({ type: 'notes/updated', paperId, loadRevision: revision, mutationGeneration, note }); return note;
    } catch (error) { throw error; }
  }, [state.activePaperId, state.loadRevision]);

  const deleteNote = useCallback(async (noteId: string) => {
    if (state.activePaperId === null) return false;
    const paperId = state.activePaperId; const revision = state.loadRevision;
    try { await paperApi.deleteNote(paperId, noteId, { signal: workspaceLifetimeController.current.signal }); const mutationGeneration = notesMutationGeneration.current + 1; notesMutationGeneration.current = mutationGeneration; dispatch({ type: 'notes/deleted', paperId, loadRevision: revision, mutationGeneration, noteId }); return true; }
    catch { return false; }
  }, [state.activePaperId, state.loadRevision]);

  const clearActiveSource = useCallback(() => {
    dispatch({ type: 'source/selected', source: null });
  }, []);

  const setSelection = useCallback((draft: TextAnchorDraft, toolbarRect: DOMRect) => {
    dispatch({ type: 'selection/set', draft, toolbarRect });
  }, []);

  const clearSelection = useCallback(() => {
    dispatch({ type: 'selection/clear' });
  }, []);

  const createHighlight = useCallback(async () => {
    if (state.activePaperId === null || state.selection === null) return null;
    const paperId = state.activePaperId;
    const requestLoadRevision = state.loadRevision;
    try {
      const highlight = await paperApi.createHighlight(paperId, {
        ...state.selection.draft,
        color: 'yellow',
        request_id: newRequestId(),
      });
      const mutationGeneration = highlightsMutationGeneration.current + 1;
      highlightsMutationGeneration.current = mutationGeneration;
      dispatch({
        type: 'highlight/created', paperId, loadRevision: requestLoadRevision, mutationGeneration, highlight,
      });
      return highlight;
    } catch (error) {
      dispatch({
        type: 'request/failed',
        paperId,
        loadRevision: requestLoadRevision,
        message: `高亮保存失败：${publicWorkspaceError(error, '请稍后重试。')}`,
      });
      return null;
    }
  }, [state.activePaperId, state.loadRevision, state.selection]);

  const deleteHighlight = useCallback(async (highlightId: string) => {
    if (state.activePaperId === null) return false;
    const paperId = state.activePaperId;
    const requestLoadRevision = state.loadRevision;
    try {
      await paperApi.deleteHighlight(paperId, highlightId);
      const mutationGeneration = highlightsMutationGeneration.current + 1;
      highlightsMutationGeneration.current = mutationGeneration;
      dispatch({
        type: 'highlight/deleted', paperId, loadRevision: requestLoadRevision, mutationGeneration, highlightId,
      });
      return true;
    } catch (error) {
      dispatch({
        type: 'request/failed',
        paperId,
        loadRevision: requestLoadRevision,
        message: `取消高亮失败：${publicWorkspaceError(error, '请稍后重试。')}`,
      });
      return false;
    }
  }, [state.activePaperId, state.loadRevision]);

  const changeHighlightColor = useCallback(async (highlightId: string, color: HighlightColor) => {
    if (state.activePaperId === null) return null;
    const paperId = state.activePaperId;
    const requestLoadRevision = state.loadRevision;
    try {
      const highlight = await paperApi.updateHighlight(
        paperId,
        highlightId,
        { color },
        { signal: workspaceLifetimeController.current.signal },
      );
      const mutationGeneration = highlightsMutationGeneration.current + 1;
      highlightsMutationGeneration.current = mutationGeneration;
      dispatch({
        type: 'highlight/updated', paperId, loadRevision: requestLoadRevision, mutationGeneration, highlight,
      });
      return highlight;
    } catch (error) {
      if (!isAbortError(error)) {
        dispatch({
          type: 'request/failed',
          paperId,
          loadRevision: requestLoadRevision,
          message: `修改高亮颜色失败：${publicWorkspaceError(error, '请稍后重试。')}`,
        });
      }
      return null;
    }
  }, [state.activePaperId, state.loadRevision]);

  return {
    ...state,
    askAgent,
    selectConversation,
    saveNote,
    selectCitation,
    selectElementSource,
    selectAnchorSource,
    updateNote,
    deleteNote,
    clearActiveSource,
    setSelection,
    clearSelection,
    createHighlight,
    deleteHighlight,
    changeHighlightColor,
    runSelectionAssist,
  };
}
