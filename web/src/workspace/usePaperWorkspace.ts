import { useCallback, useEffect, useReducer } from 'react';

import { ApiError, paperApi } from '../api/client';
import type { AgentMode, Citation } from '../api/types';
import {
  initialWorkspaceState,
  toSourceTarget,
  workspaceReducer,
} from './reducer';

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export function usePaperWorkspace(activePaperId: string | null) {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState);

  const reportApiError = useCallback((
    paperId: string,
    error: unknown,
    fallback = 'Unable to load the paper workspace.',
  ) => {
    if (isAbortError(error)) {
      return;
    }
    dispatch({
      type: 'request/failed',
      paperId,
      message: error instanceof ApiError
        ? error.message
        : fallback,
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    dispatch({ type: 'paper/opened', paperId: activePaperId });

    if (activePaperId === null) {
      return () => controller.abort();
    }

    void Promise.all([
      paperApi.getDocument(activePaperId, { signal: controller.signal }),
      paperApi.getGraph(activePaperId, { signal: controller.signal }),
      paperApi.getNotes(activePaperId, { signal: controller.signal }),
    ])
      .then(([document, graph, notes]) => {
        if (controller.signal.aborted) {
          return;
        }
        dispatch({ type: 'document/loaded', paperId: activePaperId, document, notes });
        dispatch({ type: 'graph/loaded', paperId: activePaperId, graph });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          reportApiError(activePaperId, error);
        }
      });

    return () => controller.abort();
  }, [activePaperId, reportApiError]);

  const buildCoreGraph = useCallback(async () => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    try {
      const graph = await paperApi.buildCoreGraph(paperId);
      dispatch({ type: 'graph/loaded', paperId, graph });
      return graph;
    } catch (error) {
      reportApiError(paperId, error, 'Unable to build the core graph.');
      throw error;
    }
  }, [reportApiError, state.activePaperId]);

  const buildDeepGraph = useCallback(async () => {
    if (state.activePaperId === null) {
      return null;
    }
    const paperId = state.activePaperId;
    try {
      const graph = await paperApi.buildDeepGraph(paperId);
      dispatch({ type: 'graph/loaded', paperId, graph });
      return graph;
    } catch (error) {
      reportApiError(paperId, error, 'Unable to build the deep graph.');
      throw error;
    }
  }, [reportApiError, state.activePaperId]);

  const askAgent = useCallback(async (content: string, mode: AgentMode) => {
    if (state.activePaperId === null) {
      return null;
    }
    try {
      const paperId = state.activePaperId;
      const message = await paperApi.askAgent(paperId, {
        content,
        mode,
        conversation_id: state.conversationId ?? undefined,
      });
      dispatch({
        type: 'conversation/set',
        paperId,
        conversationId: message.conversation_id,
        message,
      });
      return message;
    } catch (error) {
      reportApiError(state.activePaperId, error, 'Unable to receive an Agent response.');
      return null;
    }
  }, [reportApiError, state.activePaperId, state.conversationId]);

  const saveNote = useCallback(async (
    body: string,
    elementId = state.activeSource?.id,
    pageNumber = state.activeSource?.pageNumber,
  ) => {
    if (state.activePaperId === null) {
      return null;
    }
    try {
      const note = await paperApi.createNote(state.activePaperId, {
        body,
        element_id: elementId,
        page_number: pageNumber,
      });
      dispatch({ type: 'notes/created', paperId: state.activePaperId, note });
      return note;
    } catch (error) {
      reportApiError(state.activePaperId, error, 'Unable to save this note.');
      return null;
    }
  }, [reportApiError, state.activePaperId, state.activeSource]);

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
  };
}
