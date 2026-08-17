import { useState } from 'react';

import type { PaperSummary } from './api/types';
import { PaperLibrary } from './components/PaperLibrary';
import { WorkspaceShell } from './components/WorkspaceShell';
import { usePaperWorkspace } from './workspace/usePaperWorkspace';

export function App() {
  const [activePaper, setActivePaper] = useState<PaperSummary | null>(null);
  const workspace = usePaperWorkspace(activePaper?.id ?? null);

  return (
    <div className="app-shell">
      <PaperLibrary
        activePaperId={activePaper?.id ?? null}
        onPaperSelected={setActivePaper}
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
          <WorkspaceShell paper={activePaper} workspace={workspace} />
        )}
      </main>
    </div>
  );
}
