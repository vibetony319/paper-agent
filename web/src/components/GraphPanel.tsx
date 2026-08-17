import { useEffect, useRef, useState } from 'react';
import { Background, Controls, ReactFlow, type NodeMouseHandler } from '@xyflow/react';

import { ApiError, paperApi } from '../api/client';
import type { DocumentElement, PaperGraph } from '../api/types';
import {
  focusGraph,
  layoutGraph,
  overviewGraph,
} from './graphLayout';

export type GraphPanelProps = {
  paperId: string;
  graph: PaperGraph;
  documentElements?: DocumentElement[];
  onSelectEvidence: (elementId: string) => void;
  onGraphChange?: (graph: PaperGraph) => void;
};

type PendingAction = 'core' | 'deep' | 'focus' | null;

function publicErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function GraphPanel({
  paperId,
  graph,
  documentElements = [],
  onSelectEvidence,
  onGraphChange,
}: GraphPanelProps) {
  const [visibleGraph, setVisibleGraph] = useState(() => overviewGraph(graph));
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [flowGraph, setFlowGraph] = useState<{
    nodes: Awaited<ReturnType<typeof layoutGraph>>['nodes'];
    edges: Awaited<ReturnType<typeof layoutGraph>>['edges'];
  }>({ nodes: [], edges: [] });
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const currentPaperId = useRef(paperId);
  const operationId = useRef(0);
  const layoutId = useRef(0);

  currentPaperId.current = paperId;

  useEffect(() => {
    operationId.current += 1;
    setVisibleGraph(overviewGraph(graph));
    setSelectedNodeId(null);
    setPendingAction(null);
    setErrorMessage(null);
  }, [graph, paperId]);

  useEffect(() => {
    const requestId = ++layoutId.current;

    void layoutGraph(visibleGraph.nodes, visibleGraph.edges)
      .then((nextFlowGraph) => {
        if (layoutId.current === requestId) {
          setFlowGraph(nextFlowGraph);
        }
      })
      .catch(() => {
        if (layoutId.current === requestId) {
          setErrorMessage('Unable to arrange the graph.');
        }
      });
  }, [visibleGraph]);

  const selectedNode = selectedNodeId === null
    ? null
    : visibleGraph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const elementsById = new Map(documentElements.map((element) => [element.id, element]));

  const selectNode: NodeMouseHandler = (_event, flowNode) => {
    const node = visibleGraph.nodes.find(({ id }) => id === flowNode.id);
    if (node === undefined || pendingAction !== null) {
      return;
    }

    const requestId = ++operationId.current;
    const selectedPaperId = paperId;
    setSelectedNodeId(node.id);
    setPendingAction('focus');
    setErrorMessage(null);

    void paperApi.getGraphSubgraph(selectedPaperId, node.id, 1)
      .then((subgraph) => {
        if (operationId.current !== requestId || currentPaperId.current !== selectedPaperId) {
          return;
        }
        setVisibleGraph(focusGraph(subgraph, node.id));
        setPendingAction(null);
      })
      .catch((error: unknown) => {
        if (operationId.current !== requestId || currentPaperId.current !== selectedPaperId) {
          return;
        }
        setPendingAction(null);
        setErrorMessage(publicErrorMessage(error, 'Unable to load the focused graph.'));
      });
  };

  const buildGraph = (kind: Exclude<PendingAction, 'focus'>) => {
    if (pendingAction !== null) {
      return;
    }

    const requestId = ++operationId.current;
    const selectedPaperId = paperId;
    setPendingAction(kind);
    setErrorMessage(null);
    const request = kind === 'core'
      ? paperApi.buildCoreGraph(selectedPaperId)
      : paperApi.buildDeepGraph(selectedPaperId);

    void request
      .then((nextGraph) => {
        if (operationId.current !== requestId || currentPaperId.current !== selectedPaperId) {
          return;
        }
        setVisibleGraph(overviewGraph(nextGraph));
        setSelectedNodeId(null);
        setPendingAction(null);
        onGraphChange?.(nextGraph);
      })
      .catch((error: unknown) => {
        if (operationId.current !== requestId || currentPaperId.current !== selectedPaperId) {
          return;
        }
        setPendingAction(null);
        setErrorMessage(publicErrorMessage(
          error,
          kind === 'core' ? 'Unable to build the core graph.' : 'Unable to build the deep graph.',
        ));
      });
  };

  return (
    <section className="graph-panel" aria-labelledby="graph-panel-title">
      <header className="graph-panel__header">
        <div>
          <p className="graph-panel__eyebrow">Evidence graph</p>
          <h2 id="graph-panel-title">Paper connections</h2>
        </div>
        <div className="graph-panel__actions" aria-label="Graph build actions">
          <button
            type="button"
            onClick={() => buildGraph('core')}
            disabled={pendingAction !== null}
          >
            {pendingAction === 'core' ? 'Building core graph…' : 'Build core graph'}
          </button>
          <button
            type="button"
            onClick={() => buildGraph('deep')}
            disabled={pendingAction !== null}
          >
            {pendingAction === 'deep' ? 'Building deep graph…' : 'Build deep graph'}
          </button>
        </div>
      </header>

      {errorMessage !== null && <p className="graph-panel__error" role="alert">{errorMessage}</p>}
      {pendingAction === 'focus' && <p className="graph-panel__status">Loading focused graph…</p>}

      {visibleGraph.nodes.length === 0 ? (
        <div className="graph-panel__empty">
          <p>No graph connections are available yet.</p>
          <p>Build a core graph when the paper has finished processing.</p>
        </div>
      ) : (
        <div className="graph-panel__canvas" aria-label="Paper graph canvas">
          <ReactFlow
            nodes={flowGraph.nodes}
            edges={flowGraph.edges}
            onNodeClick={selectNode}
            fitView
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={18} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}

      <aside className="graph-panel__inspector" aria-live="polite">
        {selectedNode === null ? (
          <p>Select a graph node to inspect its evidence.</p>
        ) : (
          <>
            <p className="graph-panel__eyebrow">{selectedNode.node_type}</p>
            <h3>{selectedNode.name}</h3>
            <p>{selectedNode.summary}</p>
            <div className="graph-panel__evidence" aria-label="Node evidence">
              {selectedNode.evidence_element_ids.length === 0 ? (
                <p>No linked evidence is available for this node.</p>
              ) : selectedNode.evidence_element_ids.map((elementId, index) => {
                const element = elementsById.get(elementId);
                const located = element?.location_status === 'located'
                  && element.page_number !== null
                  && element.bbox !== null;
                const label = located
                  ? `Evidence ${index + 1}`
                  : `Evidence ${index + 1}: No source location`;

                return (
                  <button
                    key={elementId}
                    type="button"
                    disabled={!located}
                    onClick={() => onSelectEvidence(elementId)}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </aside>
    </section>
  );
}
