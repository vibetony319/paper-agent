import { useEffect, useRef, useState } from 'react';

import type { ModelProfile, PaperSummary } from '../api/types';
import type { usePaperWorkspace } from '../workspace/usePaperWorkspace';
import { paperStageSummary } from './PaperLibrary';
import { PdfReader } from './PdfReader';
import { ResizableSplit } from './ResizableSplit';
import { RightPanel } from './RightPanel';
import type { ComposerAttachment } from './ChatComposer';
import { toAnchorSourceTarget } from '../workspace/sourceTarget';

type PaperWorkspace = ReturnType<typeof usePaperWorkspace>;

export interface WorkspaceShellProps {
  paper: PaperSummary;
  workspace: PaperWorkspace;
  onRetryPaperLoading: () => void;
  onReturnToLibrary: () => void;
  onOpenModelSettings: () => void;
  onDeleteRequested: (paper: PaperSummary) => void;
  modelProfiles: ModelProfile[];
  selectedModelProfileId: string | null;
  onSelectedModelProfileIdChange: (id: string) => void;
}

export function WorkspaceShell({
  paper,
  workspace,
  onRetryPaperLoading,
  onReturnToLibrary,
  onOpenModelSettings,
  onDeleteRequested,
  modelProfiles,
  selectedModelProfileId,
  onSelectedModelProfileIdChange,
}: WorkspaceShellProps) {
  const [paperActionsOpen, setPaperActionsOpen] = useState(false);
  const paperActionsButtonRef = useRef<HTMLButtonElement>(null);
  const deletePaperItemRef = useRef<HTMLButtonElement>(null);
  const [composerAttachment, setComposerAttachment] = useState<(ComposerAttachment & { paperId: string }) | null>(null);
  const [composerFocusRequest, setComposerFocusRequest] = useState(0);
  const nextComposerAttachmentToken = useRef(0);
  const ownsActivePaper = workspace.activePaperId === paper.id;
  const document = ownsActivePaper ? workspace.document : null;
  const graph = ownsActivePaper ? workspace.graph : null;
  const blockingError = ownsActivePaper && (document === null || graph === null)
    ? workspace.errorMessage
    : null;

  const closePaperActions = () => {
    setPaperActionsOpen(false);
    paperActionsButtonRef.current?.focus();
  };

  useEffect(() => {
    if (paperActionsOpen) {
      deletePaperItemRef.current?.focus();
    }
  }, [paperActionsOpen]);

  useEffect(() => {
    setComposerAttachment(null);
    setComposerFocusRequest(0);
  }, [paper.id]);

  const currentComposerAttachment = composerAttachment?.paperId === paper.id
    ? composerAttachment
    : null;

  return (
    <div className="workspace-shell">
      <header className="workspace-topbar">
        <button type="button" onClick={onReturnToLibrary}>返回论文库</button>
        <div>
          <h1>{paper.original_filename}</h1>
        </div>
        <p className="workspace-topbar__summary">处理状态：{paperStageSummary(paper)}</p>
        <button type="button" onClick={onOpenModelSettings}>模型设置</button>
        <div className="workspace-topbar__paper-actions">
          <button
            ref={paperActionsButtonRef}
            type="button"
            aria-haspopup="menu"
            aria-expanded={paperActionsOpen}
            onClick={() => setPaperActionsOpen((current) => !current)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                setPaperActionsOpen(true);
              } else if (event.key === 'Escape' && paperActionsOpen) {
                event.preventDefault();
                closePaperActions();
              }
            }}
          >
            论文操作
          </button>
          {paperActionsOpen && <div role="menu" aria-label="论文操作">
            <button
              ref={deletePaperItemRef}
              type="button"
              role="menuitem"
              onClick={() => {
                setPaperActionsOpen(false);
                paperActionsButtonRef.current?.focus();
                onDeleteRequested(paper);
              }}
              onKeyDown={(event) => {
                if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                  event.preventDefault();
                  deletePaperItemRef.current?.focus();
                } else if (event.key === 'Escape') {
                  event.preventDefault();
                  closePaperActions();
                }
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
          <ResizableSplit
            paper={(
              <div className="workspace-pane workspace-pane--reader">
              <PdfReader
                paperId={paper.id}
                pages={document.pages}
                activeSource={workspace.activeSource}
                onSourceCleared={workspace.clearActiveSource}
                highlights={workspace.highlights}
                selection={workspace.selection}
                selectionErrorMessage={workspace.errorMessage}
                onSelectionSet={workspace.setSelection}
                onSelectionClear={workspace.clearSelection}
                onCreateHighlight={() => { void workspace.createHighlight(); }}
                onDeleteHighlight={(highlightId) => { void workspace.deleteHighlight(highlightId); }}
                selectedModelProfileId={selectedModelProfileId}
                runSelectionAssist={workspace.runSelectionAssist}
                selectionActions={{
                  ask: (draft) => {
                    nextComposerAttachmentToken.current += 1;
                    setComposerAttachment({
                      paperId: paper.id,
                      draft,
                      token: String(nextComposerAttachmentToken.current),
                    });
                    setComposerFocusRequest((current) => current + 1);
                  },
                }}
                onCreateSelectionNote={(body, draft) => workspace.saveNote(
                  body, undefined, undefined, draft,
                )}
              />
              </div>
            )}
            tools={(
              <div className="workspace-pane workspace-pane--tools">
              <RightPanel
                paperId={paper.id}
                agent={{
                  exchanges: workspace.exchanges,
                  onSelectCitation: workspace.selectCitation,
                  onSelectNoteReference: (noteId) => {
                    const note = workspace.notes.find((item) => item.id === noteId);
                    const anchor = note?.anchor_ids?.map((id) => workspace.anchors.find((item) => item.id === id)).find(Boolean);
                    const source = anchor === undefined ? null : toAnchorSourceTarget(anchor);
                    if (source !== null) workspace.selectAnchorSource(source);
                    else if (note?.element_id !== null && note?.element_id !== undefined) workspace.selectGraphEvidenceElement(note.element_id);
                  },
                }}
                graph={{
                  graph,
                  documentElements: document.elements,
                  onSelectEvidence: workspace.selectGraphEvidenceElement,
                  selectedModelProfileId,
                  stageModel: paper.stage3_model ?? paper.stage2_model ?? null,
                  buildCoreGraph: workspace.buildCoreGraph,
                  buildDeepGraph: workspace.buildDeepGraph,
                }}
                notes={{
                  activeSource: workspace.activeSource,
                  notes: workspace.notes,
                  anchors: workspace.anchors,
                  documentElements: document.elements,
                  saveNote: workspace.saveNote,
                  onSelectSource: workspace.selectGraphEvidenceElement,
                  onSelectAnchor: workspace.selectAnchorSource,
                  updateNote: workspace.updateNote,
                  deleteNote: workspace.deleteNote,
                }}
                composer={{
                  profiles: modelProfiles,
                  selectedModelProfileId,
                  onSelectedModelProfileIdChange,
                  askAgent: workspace.askAgent,
                  attachment: currentComposerAttachment,
                  onAttachmentClear: (token) => setComposerAttachment((current) => (
                    current?.paperId === paper.id && current.token === token ? null : current
                  )),
                  focusRequest: composerFocusRequest,
                }}
              />
              </div>
            )}
          />
        </>
      )}
    </div>
  );
}
