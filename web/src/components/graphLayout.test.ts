import { expect, it } from 'vitest';

import type { PaperGraph } from '../api/types';
import { focusGraph, layoutGraph, overviewGraph } from './graphLayout';

function graphWithNodes(count: number): PaperGraph {
  const nodes = Array.from({ length: count }, (_, index) => ({
    id: `node-${String(index).padStart(2, '0')}`,
    node_type: 'concept',
    name: `Node ${index}`,
    summary: `Summary ${index}`,
    stage: 'core' as const,
    evidence_element_ids: [],
  }));

  return {
    nodes,
    edges: nodes.slice(1).map((node, index) => ({
      id: `edge-${index}`,
      source_node_id: nodes[0].id,
      target_node_id: node.id,
      relation_type: 'related_to',
      stage: 'core' as const,
      evidence_element_ids: [],
    })),
  };
}

const sampleGraph: PaperGraph = {
  nodes: [
    {
      id: 'method', node_type: 'method', name: 'Method', summary: 'Routes tokens.',
      stage: 'core', evidence_element_ids: ['element-1'],
    },
    {
      id: 'router', node_type: 'component', name: 'Router', summary: 'Selects experts.',
      stage: 'deep', evidence_element_ids: ['element-2'],
    },
    {
      id: 'claim', node_type: 'claim', name: 'Claim', summary: 'Improves routing.',
      stage: 'core', evidence_element_ids: [],
    },
    {
      id: 'dataset', node_type: 'dataset', name: 'Dataset', summary: 'Evaluation data.',
      stage: 'core', evidence_element_ids: [],
    },
  ],
  edges: [
    {
      id: 'method-router', source_node_id: 'method', target_node_id: 'router',
      relation_type: 'part_of', stage: 'deep', evidence_element_ids: [],
    },
    {
      id: 'claim-method', source_node_id: 'claim', target_node_id: 'method',
      relation_type: 'supports', stage: 'core', evidence_element_ids: [],
    },
    {
      id: 'dataset-claim', source_node_id: 'dataset', target_node_id: 'claim',
      relation_type: 'evaluated_on', stage: 'core', evidence_element_ids: [],
    },
  ],
};

it('caps the overview to 24 nodes and only retains edges with visible endpoints', () => {
  const view = overviewGraph(graphWithNodes(30));
  const visibleIds = new Set(view.nodes.map((node) => node.id));

  expect(view.nodes).toHaveLength(24);
  expect(view.nodes[0]?.id).toBe('node-00');
  expect(view.edges.every((edge) => (
    visibleIds.has(edge.source_node_id) && visibleIds.has(edge.target_node_id)
  ))).toBe(true);
});

it('breaks equal-degree overview ties by stable node ID', () => {
  const view = overviewGraph({
    nodes: [
      { ...sampleGraph.nodes[0], id: 'zeta' },
      { ...sampleGraph.nodes[0], id: 'alpha' },
    ],
    edges: [],
  });

  expect(view.nodes.map((node) => node.id)).toEqual(['alpha', 'zeta']);
});

it('keeps the selected node and its one-hop neighbors in a focus graph', () => {
  const view = focusGraph(sampleGraph, 'method');

  expect(view.nodes.map((node) => node.id))
    .toEqual(expect.arrayContaining(['method', 'router', 'claim']));
  expect(view.nodes.map((node) => node.id)).not.toContain('dataset');
  expect(view.edges.map((edge) => edge.id)).toEqual(['method-router', 'claim-method']);
});

it('uses stable API IDs and positioned fixed-size nodes after ELK layout', async () => {
  const laidOut = await layoutGraph(
    focusGraph(sampleGraph, 'method').nodes,
    focusGraph(sampleGraph, 'method').edges,
  );

  expect(laidOut.nodes.map((node) => node.id)).toEqual(['method', 'router', 'claim']);
  expect(laidOut.edges.map((edge) => edge.id)).toEqual(['method-router', 'claim-method']);
  expect(laidOut.nodes[0]?.data).toMatchObject({ label: 'Method' });
  expect(laidOut.nodes.every((node) => (
    Number.isFinite(node.position.x)
    && Number.isFinite(node.position.y)
    && node.width === 216
    && node.height === 92
  ))).toBe(true);
});
