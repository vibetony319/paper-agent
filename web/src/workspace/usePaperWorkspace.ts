import { useCallback, useEffect, useReducer, useRef } from 'react';

import { paperApi, publicApiMessage } from '../api/client';
import { streamSelectionAssist } from '../api/sse';
import type { AgentMode, Citation, PaperSummary, SelectionAssistAction, TextAnchorDraft } from '../api/types';
import {
  initialWorkspaceState,
  toSourceTarget,
  workspaceReducer,
} from './reducer';

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function publicWorkspaceError(_error: unknown, fallback: string): string {
  return publicApiMessage(fallback);
}

function selectionAssistFailureMessage(action: SelectionAssistAction): string {
  return action === 'translate' ? '翻译请求失败，请重试。' : '解释请求失败，请重试。';
}

export function usePaperWorkspace(
  activePaperId: string | null,
  loadRevision = 0,
  onPaperSummaryUpdated?: (paper: PaperSummary) => void,
) {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState);
  // Keep reducer guards unique even when callers reset their retry trigger.
  const nextLoadGeneration = useRef(0);
  const currentPaperId = useRef(activePaperId);
  const currentWorkspaceRevision = useRef(loadRevision);
  const currentLoadGeneration = useRef(state.loadRevision);
  const highlightsMutationGeneration = useRef(0);
  const notesMutationGeneration = useRef(0);
  const anchorsMutationGeneration = useRef(0);
  const workspaceLifetimeController = useRef(new AbortController());

  currentPaperId.current = activePaperId;
  currentWorkspaceRevision.current = loadRevision;
  currentLoadGeneration.current = state.loadRevision;

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

  const isCurrentPaperRequest = useCallback((
    paperId: string,
    requestLoadGeneration: number,
    requestWorkspaceRevision: number,
  ) => (
    currentPaperId.current === paperId
    && currentLoadGeneration.current === requestLoadGeneration
    && currentWorkspaceRevision.current === requestWorkspaceRevision
  ), []);

  const refreshPaperSummary = useCallback(async (
    paperId: string,
    requestLoadGeneration: number,
    requestWorkspaceRevision: number,
  ) => {
    try {
      const paper = await paperApi.getPaper(paperId);
      if (
        onPaperSummaryUpdated !== undefined
        && isCurrentPaperRequest(paperId, requestLoadGeneration, requestWorkspaceRevision)
      ) {
        onPaperSummaryUpdated?.(paper);
      }
    } catch (error) {
      if (
        onPaperSummaryUpdated !== undefined
        && isCurrentPaperRequest(paperId, requestLoadGeneration, requestWorkspaceRevision)
      ) {
        reportApiError(
          paperId,
          requestLoadGeneration,
          error,
          '论文处理状态暂时无法刷新。',
        );
      }
    }
  }, [isCurrentPaperRequest, onPaperSummaryUpdated, reportApiError]);

  useEffect(() => {
    const loadGeneration = nextLoadGeneration.current++;
    const controller = new AbortController();
    highlightsMutationGeneration.current = 0;
    notesMutationGeneration.current = 0;
    anchorsMutationGeneration.current = 0;
    workspaceLifetimeController.current.abort();
    const lifetimeController = new AbortController();
    workspaceLifetimeController.current = lifetimeController;
    const annotationsMutationGeneration = highlightsMutationGeneration.current;
    dispatch({ type: 'paper/opened', paperId: activePaperId, loadRevision: loadGeneration });

    if (activePaperId === null) {
      return () => controller.abort();
    }

    void Promise.all([
      paperApi.getDocument(activePaperId, { signal: controller.signal }),
      paperApi.getGraph(activePaperId, { signal: controller.signal }),
    ])
      .then(([document, graph]) => {
        if (controller.signal.aborted) {
          return;
        }
        dispatch({
          type: 'workspace/loaded',
          paperId: activePaperId,
          loadRevision: loadGeneration,
          document,
          graph,
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
  }, [activePaperId, loadRevision]);

  const buildCoreGraph = useCallback(async (modelProfileId: string) => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    const requestLoadGeneration = state.loadRevision;
    const requestWorkspaceRevision = loadRevision;
    try {
      const graph = await paperApi.buildCoreGraph(paperId, {
        model_profile_id: modelProfileId,
        request_id: crypto.randomUUID(),
      });
      dispatch({
        type: 'graph/loaded', paperId, loadRevision: requestLoadGeneration, graph,
      });
      if (
        onPaperSummaryUpdated !== undefined
        && isCurrentPaperRequest(paperId, requestLoadGeneration, requestWorkspaceRevision)
      ) {
        await refreshPaperSummary(paperId, requestLoadGeneration, requestWorkspaceRevision);
      }
      return graph;
    } catch (error) {
      reportApiError(
        paperId,
        requestLoadGeneration,
        error,
        '暂时无法构建核心图谱。',
      );
      throw error;
    }
  }, [
    isCurrentPaperRequest,
    onPaperSummaryUpdated,
    refreshPaperSummary,
    reportApiError,
    loadRevision,
    state.activePaperId,
    state.loadRevision,
  ]);

  const buildDeepGraph = useCallback(async (modelProfileId: string) => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    const requestLoadGeneration = state.loadRevision;
    const requestWorkspaceRevision = loadRevision;
    try {
      const graph = await paperApi.buildDeepGraph(paperId, {
        model_profile_id: modelProfileId,
        request_id: crypto.randomUUID(),
      });
      dispatch({
        type: 'graph/loaded', paperId, loadRevision: requestLoadGeneration, graph,
      });
      if (
        onPaperSummaryUpdated !== undefined
        && isCurrentPaperRequest(paperId, requestLoadGeneration, requestWorkspaceRevision)
      ) {
        await refreshPaperSummary(paperId, requestLoadGeneration, requestWorkspaceRevision);
      }
      return graph;
    } catch (error) {
      reportApiError(
        paperId,
        requestLoadGeneration,
        error,
        '暂时无法构建深度图谱。',
      );
      throw error;
    }
  }, [
    isCurrentPaperRequest,
    onPaperSummaryUpdated,
    refreshPaperSummary,
    reportApiError,
    loadRevision,
    state.activePaperId,
    state.loadRevision,
  ]);

  const askAgent = useCallback(async (
    content: string,
    mode: AgentMode,
    modelProfileId: string,
    selection?: TextAnchorDraft,
  ) => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    const requestLoadRevision = state.loadRevision;
    const conversationId = state.conversationMode === mode ? state.conversationId : null;
    try {
      const message = await paperApi.askAgent(paperId, {
        content,
        mode,
        conversation_id: conversationId ?? undefined,
        model_profile_id: modelProfileId,
        request_id: crypto.randomUUID(),
        ...(selection === undefined ? {} : { selection }),
      });
      dispatch({
        type: 'conversation/set',
        paperId,
        loadRevision: requestLoadRevision,
        conversationId: message.conversation_id,
        mode,
        question: content,
        message,
      });
      return message;
    } catch (error) {
      reportApiError(
        paperId,
        requestLoadRevision,
        error,
        '暂时无法获取助手回答。',
      );
      return null;
    }
  }, [
    reportApiError,
    state.activePaperId,
    state.conversationId,
    state.conversationMode,
    state.loadRevision,
  ]);

  const saveNote = useCallback(async (
    body: string,
    elementId = state.activeSource?.id,
    pageNumber = state.activeSource?.pageNumber,
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
        ...(anchor === undefined ? {} : { anchor, request_id: crypto.randomUUID() }),
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
  }, [state.activePaperId, state.activeSource, state.loadRevision]);

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
        if (event.event === 'error') return { status: 'failed' as const, text, message: publicApiMessage(selectionAssistFailureMessage(action)) };
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
    const element = state.document?.elements.find(({ id }) => id === citation.id);
    dispatch({ type: 'source/selected', source: element === undefined ? null : toSourceTarget(element) });
  }, [state.document]);

  const selectGraphEvidenceElement = useCallback((elementId: string) => {
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

  const setGraphFocus = useCallback((nodeId: string | null) => {
    dispatch({ type: 'graph/focused', nodeId });
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
        request_id: crypto.randomUUID(),
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
        message: `删除高亮失败：${publicWorkspaceError(error, '请稍后重试。')}`,
      });
      return false;
    }
  }, [state.activePaperId, state.loadRevision]);

  return {
    ...state,
    buildCoreGraph,
    buildDeepGraph,
    askAgent,
    saveNote,
    selectCitation,
    selectGraphEvidenceElement,
    selectAnchorSource,
    updateNote,
    deleteNote,
    clearActiveSource,
    setGraphFocus,
    setSelection,
    clearSelection,
    createHighlight,
    deleteHighlight,
    runSelectionAssist,
  };
}
