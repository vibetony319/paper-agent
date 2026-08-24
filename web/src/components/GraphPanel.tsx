import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, Controls, ReactFlow, type NodeMouseHandler } from '@xyflow/react';

import { ApiError, paperApi } from '../api/client';
import type { DocumentElement, ModelSnapshot, PaperGraph } from '../api/types';
import { hasValidSourceLocation } from '../workspace/sourceTarget';
import {
  focusGraph,
  layoutGraph,
  overviewGraph,
  type GraphView,
} from './graphLayout';

export type GraphPanelProps = {
  paperId: string;
  graph: PaperGraph;
  documentElements?: DocumentElement[];
  onSelectEvidence: (elementId: string) => void;
  selectedModelProfileId: string | null;
  stageModel?: ModelSnapshot | null;
  buildCoreGraph: (modelProfileId: string) => Promise<PaperGraph | null>;
  buildDeepGraph: (modelProfileId: string) => Promise<PaperGraph | null>;
};

type PendingAction = 'core' | 'deep' | 'focus' | null;
type RootIdentity = { paperId: string; graph: PaperGraph };
type ViewIdentity = object;

type VisibleGraphState = {
  rootIdentity: RootIdentity;
  viewIdentity: ViewIdentity;
  graph: GraphView;
};

type FlowGraphState = {
  rootIdentity: RootIdentity | null;
  viewIdentity: ViewIdentity | null;
  nodes: Awaited<ReturnType<typeof layoutGraph>>['nodes'];
  edges: Awaited<ReturnType<typeof layoutGraph>>['edges'];
};

function publicErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function GraphPanel({
  paperId,
  graph,
  documentElements = [],
  onSelectEvidence,
  selectedModelProfileId,
  stageModel = null,
  buildCoreGraph,
  buildDeepGraph,
}: GraphPanelProps) {
  const rootIdentity = useMemo<RootIdentity>(() => ({ paperId, graph }), [graph, paperId]);
  const rootOverview = useMemo(() => overviewGraph(graph), [graph]);
  const [visibleGraphState, setVisibleGraphState] = useState<VisibleGraphState>(() => ({
    rootIdentity,
    viewIdentity: {},
    graph: rootOverview,
  }));
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [flowGraph, setFlowGraph] = useState<FlowGraphState>({
    rootIdentity: null,
    viewIdentity: null,
    nodes: [],
    edges: [],
  });
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const currentPaperId = useRef(paperId);
  const currentRootIdentity = useRef(rootIdentity);
  const appliedRootIdentity = useRef(rootIdentity);
  const operationId = useRef(0);
  const layoutId = useRef(0);

  currentPaperId.current = paperId;
  currentRootIdentity.current = rootIdentity;
  const rootIsCurrent = visibleGraphState.rootIdentity === rootIdentity;
  const visibleGraph = rootIsCurrent ? visibleGraphState.graph : rootOverview;
  const currentViewIdentity = useRef<ViewIdentity | null>(visibleGraphState.viewIdentity);
  currentViewIdentity.current = rootIsCurrent ? visibleGraphState.viewIdentity : null;
  const flowIsCurrent = rootIsCurrent
    && flowGraph.rootIdentity === rootIdentity
    && flowGraph.viewIdentity === visibleGraphState.viewIdentity;

  useEffect(() => {
    if (appliedRootIdentity.current === rootIdentity) {
      return;
    }
    appliedRootIdentity.current = rootIdentity;
    operationId.current += 1;
    setVisibleGraphState({ rootIdentity, viewIdentity: {}, graph: rootOverview });
    setSelectedNodeId(null);
    setPendingAction(null);
    setErrorMessage(null);
  }, [rootIdentity, rootOverview]);

  useEffect(() => {
    const requestId = ++layoutId.current;
    const layoutRootIdentity = visibleGraphState.rootIdentity;
    const layoutViewIdentity = visibleGraphState.viewIdentity;

    void layoutGraph(visibleGraphState.graph.nodes, visibleGraphState.graph.edges)
      .then((nextFlowGraph) => {
        if (
          layoutId.current === requestId
          && currentRootIdentity.current === layoutRootIdentity
          && currentViewIdentity.current === layoutViewIdentity
        ) {
          setFlowGraph({
            rootIdentity: layoutRootIdentity,
            viewIdentity: layoutViewIdentity,
            ...nextFlowGraph,
          });
        }
      })
      .catch(() => {
        if (
          layoutId.current === requestId
          && currentRootIdentity.current === layoutRootIdentity
          && currentViewIdentity.current === layoutViewIdentity
        ) {
          setErrorMessage('暂时无法整理图谱。');
        }
      });
  }, [visibleGraphState]);

  const selectedNode = selectedNodeId === null || !rootIsCurrent
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
    const selectedRootIdentity = rootIdentity;
    setSelectedNodeId(node.id);
    setPendingAction('focus');
    setErrorMessage(null);

    void paperApi.getGraphSubgraph(selectedPaperId, node.id, 1)
      .then((subgraph) => {
        if (
          operationId.current !== requestId
          || currentPaperId.current !== selectedPaperId
          || currentRootIdentity.current !== selectedRootIdentity
        ) {
          return;
        }
        setVisibleGraphState({
          rootIdentity: selectedRootIdentity,
          viewIdentity: {},
          graph: focusGraph(subgraph, node.id),
        });
        setPendingAction(null);
      })
      .catch((error: unknown) => {
        if (
          operationId.current !== requestId
          || currentPaperId.current !== selectedPaperId
          || currentRootIdentity.current !== selectedRootIdentity
        ) {
          return;
        }
        setPendingAction(null);
        setErrorMessage(publicErrorMessage(error, '暂时无法加载聚焦图谱。'));
      });
  };

  const buildGraph = (kind: Exclude<PendingAction, 'focus'>) => {
    if (pendingAction !== null || selectedModelProfileId === null) {
      return;
    }

    const requestId = ++operationId.current;
    const selectedPaperId = paperId;
    const selectedRootIdentity = rootIdentity;
    setPendingAction(kind);
    setErrorMessage(null);
    const request = kind === 'core'
      ? buildCoreGraph(selectedModelProfileId)
      : buildDeepGraph(selectedModelProfileId);

    void request
      .then((nextGraph) => {
        if (
          operationId.current !== requestId
          || currentPaperId.current !== selectedPaperId
          || currentRootIdentity.current !== selectedRootIdentity
        ) {
          return;
        }
        if (nextGraph === null) {
          setPendingAction(null);
          return;
        }
        setVisibleGraphState({
          rootIdentity: selectedRootIdentity,
          viewIdentity: {},
          graph: overviewGraph(nextGraph),
        });
        setSelectedNodeId(null);
        setPendingAction(null);
      })
      .catch((error: unknown) => {
        if (
          operationId.current !== requestId
          || currentPaperId.current !== selectedPaperId
          || currentRootIdentity.current !== selectedRootIdentity
        ) {
          return;
        }
        setPendingAction(null);
        setErrorMessage(publicErrorMessage(
          error,
          kind === 'core' ? '暂时无法构建核心图谱。' : '暂时无法构建深度图谱。',
        ));
      });
  };

  return (
    <section className="graph-panel" aria-labelledby="graph-panel-title">
      <header className="graph-panel__header">
        <div>
          <p className="graph-panel__eyebrow">知识图谱</p>
          <h2 id="graph-panel-title">论文关系</h2>
        </div>
        <div className="graph-panel__actions" aria-label="图谱构建操作">
          <button
            type="button"
            onClick={() => buildGraph('core')}
            disabled={pendingAction !== null || selectedModelProfileId === null}
          >
            {pendingAction === 'core' ? '正在构建核心图谱…' : '构建核心图谱'}
          </button>
          <button
            type="button"
            onClick={() => buildGraph('deep')}
            disabled={pendingAction !== null || selectedModelProfileId === null}
          >
            {pendingAction === 'deep' ? '正在构建深度图谱…' : '构建深度图谱'}
          </button>
        </div>
      </header>

      {selectedModelProfileId === null && <p className="graph-panel__guidance">请先选择当前模型，再构建知识图谱。</p>}
      {stageModel !== null && <p className="graph-panel__stage-model">构建模型：{stageModel.display_name}</p>}

      {errorMessage !== null && <p className="graph-panel__error" role="alert">{errorMessage}</p>}
      {pendingAction === 'focus' && <p className="graph-panel__status">正在加载聚焦图谱…</p>}

      {visibleGraph.nodes.length === 0 ? (
        <div className="graph-panel__empty">
          <p>暂无可用图谱关系。</p>
          <p>论文处理完成后，可使用当前模型构建核心图谱。</p>
        </div>
      ) : (
        <div className="graph-panel__canvas" aria-label="论文图谱画布">
          <ReactFlow
            nodes={flowIsCurrent ? flowGraph.nodes : []}
            edges={flowIsCurrent ? flowGraph.edges : []}
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
          <p>选择图谱节点以查看证据。</p>
        ) : (
          <>
            <p className="graph-panel__eyebrow">{selectedNode.node_type}</p>
            <h3>{selectedNode.name}</h3>
            <p>{selectedNode.summary}</p>
            <div className="graph-panel__evidence" aria-label="节点证据">
              {selectedNode.evidence_element_ids.length === 0 ? (
                <p>此节点暂无关联证据。</p>
              ) : selectedNode.evidence_element_ids.map((elementId, index) => {
                const element = elementsById.get(elementId);
                const located = element !== undefined && hasValidSourceLocation(element);
                const label = located
                  ? `证据 ${index + 1}`
                  : `证据 ${index + 1}：原文位置不可用`;

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
