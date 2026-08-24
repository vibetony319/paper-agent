import { useState, type ReactNode } from 'react';

import type { PaperSummary } from '../api/types';
import type { usePaperWorkspace } from '../workspace/usePaperWorkspace';
import { GraphPanel } from './GraphPanel';
import { paperStageSummary } from './PaperLibrary';
import { PdfReader } from './PdfReader';
import { RightPanel } from './RightPanel';

type PaperWorkspace = ReturnType<typeof usePaperWorkspace>;

export interface WorkspaceShellProps {
  paper: PaperSummary;
  workspace: PaperWorkspace;
  onRetryPaperLoading: () => void;
  onReturnToLibrary: () => void;
  onOpenModelSettings: () => void;
  onDeleteRequested: (paper: PaperSummary) => void;
  modelSelector: ReactNode;
}

export function WorkspaceShell({
  paper,
  workspace,
  onRetryPaperLoading,
  onReturnToLibrary,
  onOpenModelSettings,
  onDeleteRequested,
  modelSelector,
}: WorkspaceShellProps) {
  const [paperActionsOpen, setPaperActionsOpen] = useState(false);
  const ownsActivePaper = workspace.activePaperId === paper.id;
  const document = ownsActivePaper ? workspace.document : null;
  const graph = ownsActivePaper ? workspace.graph : null;
  const blockingError = ownsActivePaper && (document === null || graph === null)
    ? workspace.errorMessage
    : null;

  return (
    <div className="workspace-shell">
      <header className="workspace-topbar">
        <button type="button" onClick={onReturnToLibrary}>返回论文库</button>
        <div>
          <h1>{paper.original_filename}</h1>
        </div>
        <p className="workspace-topbar__summary">处理状态：{paperStageSummary(paper)}</p>
        {modelSelector}
        <button type="button" onClick={onOpenModelSettings}>模型设置</button>
        <div className="workspace-topbar__paper-actions">
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={paperActionsOpen}
            onClick={() => setPaperActionsOpen((current) => !current)}
          >
            论文操作
          </button>
          {paperActionsOpen && <div role="menu" aria-label="论文操作">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setPaperActionsOpen(false);
                onDeleteRequested(paper);
              }}
            >
              删除论文
            </button>
          </div>}
        </div>
      </header>

      {blockingError !== null ? (
        <section className="workspace-state workspace-state--error" aria-label="论文工作区错误">
          <h2>论文阅读工作区暂不可用</h2>
          <p role="alert">{blockingError}</p>
          <p>论文仍在论文库中，可在处理完成后重试。</p>
          <button type="button" onClick={onRetryPaperLoading}>重试加载论文</button>
        </section>
      ) : document === null || graph === null ? (
        <section className="workspace-state" aria-label="论文工作区加载">
          <h2>正在准备阅读面板</h2>
          <p role="status">正在加载论文阅读工作区…</p>
          <p>文档和知识图谱准备完成后，将打开阅读器与研究工具。</p>
        </section>
      ) : (
        <>
          {workspace.errorMessage !== null && (
            <p className="workspace-shell__error" role="alert">{workspace.errorMessage}</p>
          )}
          {workspace.notesErrorMessage !== null && (
            <p className="workspace-shell__error" role="alert">
              {workspace.notesErrorMessage}
            </p>
          )}
          <div className="workspace-grid">
            <div className="workspace-pane workspace-pane--reader">
              <PdfReader
                paperId={paper.id}
                pages={document.pages}
                activeSource={workspace.activeSource}
                onSourceCleared={workspace.clearActiveSource}
              />
            </div>
            <section className="workspace-pane workspace-pane--graph" aria-label="Paper graph">
              <GraphPanel
                paperId={paper.id}
                graph={graph}
                documentElements={document.elements}
                onSelectEvidence={workspace.selectGraphEvidenceElement}
                buildCoreGraph={workspace.buildCoreGraph}
                buildDeepGraph={workspace.buildDeepGraph}
              />
            </section>
            <div className="workspace-pane workspace-pane--tools">
              <RightPanel
                paperId={paper.id}
                agent={{
                  messages: workspace.messages,
                  askAgent: workspace.askAgent,
                  onSelectCitation: workspace.selectCitation,
                }}
                notes={{
                  activeSource: workspace.activeSource,
                  notes: workspace.notes,
                  documentElements: document.elements,
                  saveNote: workspace.saveNote,
                  onSelectSource: workspace.selectGraphEvidenceElement,
                }}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
