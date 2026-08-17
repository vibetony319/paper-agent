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
}

export function WorkspaceShell({ paper, workspace }: WorkspaceShellProps) {
  const ownsActivePaper = workspace.activePaperId === paper.id;
  const document = ownsActivePaper ? workspace.document : null;
  const graph = ownsActivePaper ? workspace.graph : null;
  const blockingError = ownsActivePaper && (document === null || graph === null)
    ? workspace.errorMessage
    : null;

  return (
    <div className="workspace-shell">
      <header className="workspace-topbar">
        <div>
          <p className="workspace-topbar__label">Active paper</p>
          <h1>{paper.original_filename}</h1>
        </div>
        <p className="workspace-topbar__summary">{paperStageSummary(paper)}</p>
      </header>

      {blockingError !== null ? (
        <section className="workspace-state workspace-state--error" aria-label="Workspace error">
          <h2>Paper workspace unavailable</h2>
          <p role="alert">{blockingError}</p>
          <p>The paper remains available in the library. Try selecting it again when processing is ready.</p>
        </section>
      ) : document === null || graph === null ? (
        <section className="workspace-state" aria-label="Workspace loading">
          <h2>Preparing the research panes</h2>
          <p role="status">Loading paper workspace…</p>
          <p>The reader, graph, and research tools will open after the document and graph are ready.</p>
        </section>
      ) : (
        <>
          {workspace.errorMessage !== null && (
            <p className="workspace-shell__error" role="alert">{workspace.errorMessage}</p>
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
