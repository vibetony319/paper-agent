import { useCallback, useEffect, useReducer, useRef } from 'react';

import { ApiError, paperApi } from '../api/client';
import type { AgentMode, Citation, PaperSummary, TextAnchorDraft } from '../api/types';
import {
  initialWorkspaceState,
  toSourceTarget,
  workspaceReducer,
} from './reducer';

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
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

  currentPaperId.current = activePaperId;
  currentWorkspaceRevision.current = loadRevision;
  currentLoadGeneration.current = state.loadRevision;

  const reportApiError = useCallback((
    paperId: string,
    loadRevision: number,
    error: unknown,
    fallback = 'Unable to load the paper workspace.',
  ) => {
    if (isAbortError(error)) {
      return;
    }
    dispatch({
      type: 'request/failed',
      paperId,
      loadRevision,
      message: error instanceof ApiError
        ? error.message
        : fallback,
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
          'Unable to refresh the paper status.',
        );
      }
    }
  }, [isCurrentPaperRequest, onPaperSummaryUpdated, reportApiError]);

  useEffect(() => {
    const loadGeneration = nextLoadGeneration.current++;
    const controller = new AbortController();
    highlightsMutationGeneration.current = 0;
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
          message: error instanceof ApiError
            ? error.message
            : 'Unable to load the paper workspace.',
        });
      });

    void paperApi.getNotes(activePaperId, { signal: controller.signal })
      .then((notes) => {
        if (controller.signal.aborted) {
          return;
        }
        dispatch({
          type: 'notes/loaded', paperId: activePaperId, loadRevision: loadGeneration, notes,
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
          message: error instanceof ApiError
            ? error.message
            : 'Unable to load paper notes.',
        });
      });

    void paperApi.getAnnotations(activePaperId, { signal: controller.signal })
      .then(({ highlights }) => {
        if (controller.signal.aborted) return;
        dispatch({
          type: 'highlights/loaded', paperId: activePaperId, loadRevision: loadGeneration, highlights,
          mutationGeneration: annotationsMutationGeneration,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        dispatch({
          type: 'request/failed',
          paperId: activePaperId,
          loadRevision: loadGeneration,
          message: error instanceof ApiError ? error.message : '无法加载论文高亮。',
        });
      });

    return () => controller.abort();
  }, [activePaperId, loadRevision]);

  const buildCoreGraph = useCallback(async () => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    const requestLoadGeneration = state.loadRevision;
    const requestWorkspaceRevision = loadRevision;
    try {
      const graph = await paperApi.buildCoreGraph(paperId);
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
        'Unable to build the core graph.',
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

  const buildDeepGraph = useCallback(async () => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    const requestLoadGeneration = state.loadRevision;
    const requestWorkspaceRevision = loadRevision;
    try {
      const graph = await paperApi.buildDeepGraph(paperId);
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
        'Unable to build the deep graph.',
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

  const askAgent = useCallback(async (content: string, mode: AgentMode) => {
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
      });
      dispatch({
        type: 'conversation/set',
        paperId,
        loadRevision: requestLoadRevision,
        conversationId: message.conversation_id,
        mode,
        message,
      });
      return message;
    } catch (error) {
      reportApiError(
        paperId,
        requestLoadRevision,
        error,
        'Unable to receive an Agent response.',
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
      });
      dispatch({
        type: 'notes/created', paperId, loadRevision: requestLoadRevision, note,
      });
      return note;
    } catch (error) {
      if (!isAbortError(error)) {
        dispatch({
          type: 'notes/failed',
          paperId,
          loadRevision: requestLoadRevision,
          message: error instanceof ApiError ? error.message : 'Unable to save this note.',
        });
      }
      return null;
    }
  }, [state.activePaperId, state.activeSource, state.loadRevision]);

  const selectCitation = useCallback((citation: Citation) => {
    const element = state.document?.elements.find(({ id }) => id === citation.id);
    dispatch({ type: 'source/selected', source: element === undefined ? null : toSourceTarget(element) });
  }, [state.document]);

  const selectGraphEvidenceElement = useCallback((elementId: string) => {
    const element = state.document?.elements.find(({ id }) => id === elementId);
    dispatch({ type: 'source/selected', source: element === undefined ? null : toSourceTarget(element) });
  }, [state.document]);

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
        message: error instanceof ApiError ? `高亮保存失败：${error.message}` : '高亮保存失败，请稍后重试。',
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
        message: error instanceof ApiError ? `删除高亮失败：${error.message}` : '删除高亮失败，请稍后重试。',
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
    clearActiveSource,
    setGraphFocus,
    setSelection,
    clearSelection,
    createHighlight,
    deleteHighlight,
  };
}
