import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { ApiError, paperApi } from '../api/client';
import type { PaperDocument, PaperGraph } from '../api/types';
import { usePaperWorkspace } from './usePaperWorkspace';

const emptyGraph: PaperGraph = { nodes: [], edges: [] };
const builtGraph: PaperGraph = {
  nodes: [{
    id: 'built', node_type: 'claim', name: 'Built graph', summary: 'Workspace result.',
    stage: 'core', evidence_element_ids: [],
  }],
  edges: [],
};

function documentFor(paperId: string): PaperDocument {
  return {
    paper: { id: paperId, original_filename: `${paperId}.pdf`, status: 'completed' },
    pages: [],
    sections: [],
    elements: [],
    notes: [],
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(paperApi, 'getDocument').mockImplementation(async (paperId) => documentFor(paperId));
  vi.spyOn(paperApi, 'getGraph').mockResolvedValue(emptyGraph);
  vi.spyOn(paperApi, 'getNotes').mockResolvedValue([]);
});

afterEach(cleanup);

it('returns and stores a core graph built for the active paper', async () => {
  const buildCoreGraph = vi.spyOn(paperApi, 'buildCoreGraph').mockResolvedValue(builtGraph);
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.graph).toBe(emptyGraph));

  let returnedGraph: PaperGraph | null | undefined;
  await act(async () => {
    returnedGraph = await result.current.buildCoreGraph();
  });

  expect(buildCoreGraph).toHaveBeenCalledWith('paper-a');
  expect(returnedGraph).toBe(builtGraph);
  expect(result.current.graph).toBe(builtGraph);
});

it('returns null without calling a builder when no paper is active', async () => {
  const buildCoreGraph = vi.spyOn(paperApi, 'buildCoreGraph');
  const buildDeepGraph = vi.spyOn(paperApi, 'buildDeepGraph');
  const { result } = renderHook(() => usePaperWorkspace(null));

  let coreResult: PaperGraph | null | undefined;
  let deepResult: PaperGraph | null | undefined;
  await act(async () => {
    coreResult = await result.current.buildCoreGraph();
    deepResult = await result.current.buildDeepGraph();
  });

  expect(coreResult).toBeNull();
  expect(deepResult).toBeNull();
  expect(buildCoreGraph).not.toHaveBeenCalled();
  expect(buildDeepGraph).not.toHaveBeenCalled();
});

it('reports and rethrows a deep graph API error', async () => {
  const apiError = new ApiError(503, 'Graph service is unavailable.');
  vi.spyOn(paperApi, 'buildDeepGraph').mockRejectedValue(apiError);
  const { result } = renderHook(() => usePaperWorkspace('paper-a'));
  await waitFor(() => expect(result.current.graph).toBe(emptyGraph));

  let caught: unknown;
  await act(async () => {
    try {
      await result.current.buildDeepGraph();
    } catch (error) {
      caught = error;
    }
  });

  expect(caught).toBe(apiError);
  expect(result.current.errorMessage).toBe('Graph service is unavailable.');
  expect(result.current.graph).toBe(emptyGraph);
});
