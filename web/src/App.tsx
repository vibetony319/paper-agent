import { useState } from 'react';

import type { PaperSummary } from './api/types';
import { ModelSettingsDialog } from './components/ModelSettingsDialog';
import { ConfirmDeleteDialog } from './components/ConfirmDeleteDialog';
import { PaperLibrary } from './components/PaperLibrary';
import { WorkspaceShell } from './components/WorkspaceShell';
import { useModelProfiles } from './modelProfiles/useModelProfiles';
import { usePaperWorkspace } from './workspace/usePaperWorkspace';

export function App() {
  const [activePaper, setActivePaper] = useState<PaperSummary | null>(null);
  const [view, setView] = useState<'library' | 'reader'>('library');
  const [paperPendingDeletion, setPaperPendingDeletion] = useState<PaperSummary | null>(null);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [libraryHeadingFocusRequested, setLibraryHeadingFocusRequested] = useState(false);
  const [workspaceLoadRevision, setWorkspaceLoadRevision] = useState(0);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const workspace = usePaperWorkspace(activePaper?.id ?? null, workspaceLoadRevision);
  const modelProfiles = useModelProfiles(activePaper?.id ?? null);

  const selectPaper = (paper: PaperSummary) => {
    const isActivePaper = activePaper?.id === paper.id;
    const activeWorkspaceIsReady = workspace.activePaperId === paper.id
      && workspace.document !== null;

    if (isActivePaper && !activeWorkspaceIsReady) {
      setWorkspaceLoadRevision((current) => current + 1);
    } else if (!isActivePaper) {
      setWorkspaceLoadRevision(0);
    }
    setActivePaper(paper);
    setLibraryHeadingFocusRequested(false);
    setView('reader');
  };

  const retryActivePaper = () => {
    setWorkspaceLoadRevision((current) => current + 1);
  };

  const returnToLibrary = () => {
    setView('library');
  };

  const onPaperDeleted = (paperId: string) => {
    setPaperPendingDeletion(null);
    if (activePaper?.id === paperId) {
      setActivePaper(null);
    }
    setLibraryHeadingFocusRequested(true);
    setLibraryRevision((current) => current + 1);
    setView('library');
  };

  return (
    <div className="app-shell">
      {view === 'library' ? (
        <PaperLibrary
          key={libraryRevision}
          activePaperId={activePaper?.id ?? null}
          paperUpdate={activePaper}
          onPaperSelected={selectPaper}
          onPaperDeleteRequested={setPaperPendingDeletion}
          focusHeading={libraryHeadingFocusRequested}
        />
      ) : activePaper !== null ? (
        <WorkspaceShell
          paper={activePaper}
          workspace={workspace}
          onRetryPaperLoading={retryActivePaper}
          onReturnToLibrary={returnToLibrary}
          onOpenModelSettings={() => setModelSettingsOpen(true)}
          onDeleteRequested={setPaperPendingDeletion}
          modelProfiles={modelProfiles.profiles}
          selectedModelProfileId={modelProfiles.selectedProfileId}
          onSelectedModelProfileIdChange={modelProfiles.selectProfile}
        />
      ) : null}
      <ModelSettingsDialog
        open={modelSettingsOpen}
        profiles={modelProfiles.profiles}
        onClose={() => setModelSettingsOpen(false)}
        onRefresh={modelProfiles.refresh}
        onCreate={modelProfiles.createProfile}
        onUpdate={modelProfiles.updateProfile}
        onDelete={modelProfiles.deleteProfile}
        onTest={modelProfiles.testProfile}
      />
      <ConfirmDeleteDialog
        paper={paperPendingDeletion}
        onCancel={() => setPaperPendingDeletion(null)}
        onDeleted={onPaperDeleted}
      />
    </div>
  );
}
