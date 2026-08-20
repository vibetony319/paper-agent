import { useCallback, useState } from 'react';

import type { PaperSummary } from './api/types';
import { PaperLibrary } from './components/PaperLibrary';
import { WorkspaceShell } from './components/WorkspaceShell';
import { usePaperWorkspace } from './workspace/usePaperWorkspace';

export function App() {
  const [activePaper, setActivePaper] = useState<PaperSummary | null>(null);
  const [workspaceLoadRevision, setWorkspaceLoadRevision] = useState(0);
  const updateActivePaperSummary = useCallback((paper: PaperSummary) => {
    setActivePaper((current) => current?.id === paper.id ? paper : current);
  }, []);
  const workspace = usePaperWorkspace(
    activePaper?.id ?? null,
    workspaceLoadRevision,
    updateActivePaperSummary,
  );

  const selectPaper = (paper: PaperSummary) => {
    const isActivePaper = activePaper?.id === paper.id;
    const activeWorkspaceIsReady = workspace.activePaperId === paper.id
      && workspace.document !== null
      && workspace.graph !== null;

    if (isActivePaper && !activeWorkspaceIsReady) {
      setWorkspaceLoadRevision((current) => current + 1);
    } else if (!isActivePaper) {
      setWorkspaceLoadRevision(0);
    }
    setActivePaper(paper);
  };

  const retryActivePaper = () => {
    setWorkspaceLoadRevision((current) => current + 1);
  };

  return (
    <div className="app-shell">
      <PaperLibrary
        activePaperId={activePaper?.id ?? null}
        paperUpdate={activePaper}
        onPaperSelected={selectPaper}
      />
      <main className="app-content">
        {activePaper === null ? (
          <section className="app-empty" aria-labelledby="app-empty-title">
            <p className="app-empty__label">Paper reading workbench</p>
            <h1 id="app-empty-title">Select a paper or upload a PDF to begin.</h1>
            <p>
              The workbench keeps original pages, evidence connections, Agent answers,
              and notes in one local reading view.
            </p>
          </section>
        ) : (
          <WorkspaceShell
            paper={activePaper}
            workspace={workspace}
            onRetryPaperLoading={retryActivePaper}
          />
        )}
      </main>
    </div>
  );
}
