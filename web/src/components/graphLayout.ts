import ELK from 'elkjs/lib/elk.bundled.js';
import type { Edge as FlowEdge, Node as FlowNode } from '@xyflow/react';

import type { GraphEdge, GraphNode, PaperGraph } from '../api/types';

export const GRAPH_NODE_WIDTH = 216;
export const GRAPH_NODE_HEIGHT = 92;
const OVERVIEW_NODE_LIMIT = 24;

export type GraphView = Pick<PaperGraph, 'nodes' | 'edges'>;

export type FlowGraphNodeData = {
  node: GraphNode;
  label: string;
};

function visibleEdges(graph: PaperGraph, visibleNodeIds: ReadonlySet<string>): GraphEdge[] {
  return graph.edges.filter((edge) => (
    visibleNodeIds.has(edge.source_node_id) && visibleNodeIds.has(edge.target_node_id)
  ));
}

function degreeByNodeId(graph: PaperGraph): Map<string, number> {
  const degree = new Map(graph.nodes.map((node) => [node.id, 0]));

  for (const edge of graph.edges) {
    degree.set(edge.source_node_id, (degree.get(edge.source_node_id) ?? 0) + 1);
    degree.set(edge.target_node_id, (degree.get(edge.target_node_id) ?? 0) + 1);
  }

  return degree;
}

export function overviewGraph(graph: PaperGraph): GraphView {
  const degree = degreeByNodeId(graph);
  const nodes = [...graph.nodes]
    .sort((left, right) => (
      (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0)
      || left.id.localeCompare(right.id)
    ))
    .slice(0, OVERVIEW_NODE_LIMIT);
  const visibleNodeIds = new Set(nodes.map((node) => node.id));

  return { nodes, edges: visibleEdges(graph, visibleNodeIds) };
}

export function focusGraph(graph: PaperGraph, nodeId: string): GraphView {
  const visibleNodeIds = new Set([nodeId]);

  for (const edge of graph.edges) {
    if (edge.source_node_id === nodeId) {
      visibleNodeIds.add(edge.target_node_id);
    }
    if (edge.target_node_id === nodeId) {
      visibleNodeIds.add(edge.source_node_id);
    }
  }

  const nodes = graph.nodes.filter((node) => visibleNodeIds.has(node.id));
  const retainedNodeIds = new Set(nodes.map((node) => node.id));
  return { nodes, edges: visibleEdges(graph, retainedNodeIds) };
}

function toFlowNodes(nodes: GraphNode[]): FlowNode<FlowGraphNodeData>[] {
  return nodes.map((node) => ({
    id: node.id,
    position: { x: 0, y: 0 },
    width: GRAPH_NODE_WIDTH,
    height: GRAPH_NODE_HEIGHT,
    data: { node, label: node.name },
  }));
}

function toFlowEdges(edges: GraphEdge[]): FlowEdge[] {
  return edges.map((edge) => ({
    id: edge.id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    label: edge.relation_type,
    type: 'smoothstep',
  }));
}

export async function layoutGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
): Promise<{ nodes: FlowNode<FlowGraphNodeData>[]; edges: FlowEdge[] }> {
  const flowNodes = toFlowNodes(nodes);
  const flowEdges = toFlowEdges(edges);

  if (flowNodes.length === 0) {
    return { nodes: flowNodes, edges: flowEdges };
  }

  const elk = new ELK();
  const layout = await elk.layout({
    id: 'paper-graph',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.spacing.nodeNodeBetweenLayers': '68',
      'elk.spacing.nodeNode': '36',
    },
    children: flowNodes.map((node) => ({
      id: node.id,
      width: GRAPH_NODE_WIDTH,
      height: GRAPH_NODE_HEIGHT,
    })),
    edges: flowEdges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  });
  const positions = new Map((layout.children ?? []).map((node) => [node.id, {
    x: node.x ?? 0,
    y: node.y ?? 0,
  }]));

  return {
    nodes: flowNodes.map((node) => ({
      ...node,
      position: positions.get(node.id) ?? node.position,
    })),
    edges: flowEdges,
  };
}
