import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('@xyflow/react', async () => {
  const React = await import('react');
  return {
    ReactFlow: ({ nodes, onNodeClick }: {
      nodes: Array<{ id: string; data: { node: { name: string } } }>;
      onNodeClick: (_event: unknown, node: { id: string }) => void;
    }) => React.createElement(
      'div',
      { 'data-testid': 'graph-canvas' },
      nodes.map((node) => React.createElement(
        'button',
        {
          key: node.id,
          type: 'button',
          onClick: () => onNodeClick({}, node),
        },
        node.data.node.name,
      )),
    ),
    Background: () => null,
    Controls: () => null,
  };
});

import { paperApi } from '../api/client';
import type { DocumentElement, PaperGraph } from '../api/types';
import { GraphPanel } from './GraphPanel';

const graph: PaperGraph = {
  nodes: [
    {
      id: 'method', node_type: 'method', name: 'Token router', summary: 'Routes tokens.',
      stage: 'core', evidence_element_ids: ['located', 'unlocated'],
    },
    {
      id: 'claim', node_type: 'claim', name: 'Primary claim', summary: 'Improves quality.',
      stage: 'core', evidence_element_ids: [],
    },
  ],
  edges: [{
    id: 'claim-method', source_node_id: 'claim', target_node_id: 'method',
    relation_type: 'supports', stage: 'core', evidence_element_ids: [],
  }],
};

const elements: DocumentElement[] = [
  {
    id: 'located', kind: 'paragraph', text: 'Located source.', page_number: 2,
    bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 }, section_id: null,
    location_status: 'located', order: 1,
  },
  {
    id: 'unlocated', kind: 'paragraph', text: 'Unlocated source.', page_number: null,
    bbox: null, section_id: null, location_status: 'unlocated', order: 2,
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(cleanup);

it('loads a one-hop subgraph after node selection and offers only located evidence', async () => {
  const onSelectEvidence = vi.fn();
  vi.spyOn(paperApi, 'getGraphSubgraph').mockResolvedValue(graph);

  render(
    <GraphPanel
      paperId="paper-a"
      graph={graph}
      documentElements={elements}
      onSelectEvidence={onSelectEvidence}
    />,
  );

  fireEvent.click(await screen.findByRole('button', { name: 'Token router' }));
  expect(screen.getByText('Loading focused graph…')).toBeVisible();

  await waitFor(() => expect(paperApi.getGraphSubgraph)
    .toHaveBeenCalledWith('paper-a', 'method', 1));
  expect(screen.getByRole('heading', { name: 'Token router' })).toBeVisible();
  expect(screen.getByText('method')).toBeVisible();
  expect(screen.getByText('Routes tokens.')).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: 'Evidence 1' }));
  expect(onSelectEvidence).toHaveBeenCalledWith('located');
  expect(screen.getByRole('button', { name: 'Evidence 2: No source location' })).toBeDisabled();
});

it('keeps the last valid graph visible when a focused graph request fails safely', async () => {
  vi.spyOn(paperApi, 'getGraphSubgraph').mockRejectedValue(
    new Error('secret diagnostic from graph service'),
  );

  render(
    <GraphPanel
      paperId="paper-a"
      graph={graph}
      documentElements={elements}
      onSelectEvidence={vi.fn()}
    />,
  );

  fireEvent.click(await screen.findByRole('button', { name: 'Token router' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load the focused graph.');
  expect(screen.getByTestId('graph-canvas')).toHaveTextContent('Token router');
  expect(screen.queryByText(/secret diagnostic/i)).not.toBeInTheDocument();
});

it('ignores a focused graph response after the active paper changes', async () => {
  let resolveSubgraph: ((value: PaperGraph) => void) | undefined;
  vi.spyOn(paperApi, 'getGraphSubgraph').mockImplementation(() => new Promise<PaperGraph>((resolve) => {
    resolveSubgraph = resolve;
  }));
  const paperBGraph: PaperGraph = {
    nodes: [{
      id: 'result', node_type: 'result', name: 'Paper B result', summary: 'A different paper.',
      stage: 'core', evidence_element_ids: [],
    }],
    edges: [],
  };

  const { rerender } = render(
    <GraphPanel
      paperId="paper-a"
      graph={graph}
      documentElements={elements}
      onSelectEvidence={vi.fn()}
    />,
  );

  fireEvent.click(await screen.findByRole('button', { name: 'Token router' }));
  rerender(
    <GraphPanel
      paperId="paper-b"
      graph={paperBGraph}
      documentElements={[]}
      onSelectEvidence={vi.fn()}
    />,
  );
  resolveSubgraph?.(graph);

  expect(await screen.findByRole('button', { name: 'Paper B result' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Token router' })).not.toBeInTheDocument();
});

it('builds only on click and accepts a successful graph refresh', async () => {
  const onGraphChange = vi.fn();
  const buildCoreGraph = vi.spyOn(paperApi, 'buildCoreGraph').mockResolvedValue(graph);

  render(
    <GraphPanel
      paperId="paper-a"
      graph={{ nodes: [], edges: [] }}
      documentElements={elements}
      onSelectEvidence={vi.fn()}
      onGraphChange={onGraphChange}
    />,
  );

  expect(buildCoreGraph).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Build core graph' }));

  await waitFor(() => expect(buildCoreGraph).toHaveBeenCalledWith('paper-a'));
  expect(onGraphChange).toHaveBeenCalledWith(graph);
  expect(await screen.findByRole('button', { name: 'Token router' })).toBeVisible();
});
