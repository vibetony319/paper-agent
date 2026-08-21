import { useEffect, useRef, useState } from 'react';

import { ApiError, paperApi } from '../api/client';
import type { PaperSummary, ProcessingStatus } from '../api/types';

export interface PaperLibraryProps {
  activePaperId: string | null;
  paperUpdate: PaperSummary | null;
  onPaperSelected: (paper: PaperSummary) => void;
}

type Stage = {
  label: string;
  status: ProcessingStatus | null;
};

function statusLabel(status: ProcessingStatus | null): string {
  switch (status) {
    case 'completed':
      return 'Ready';
    case 'queued':
      return 'Pending';
    case 'running':
      return 'Running';
    case 'partial':
      return 'Partial';
    case 'failed':
      return 'Failed';
    case null:
      return 'Unavailable';
  }
}

function stagesFor(paper: PaperSummary): Stage[] {
  return [
    { label: 'Geometry', status: paper.stage0_status },
    { label: 'Structure', status: paper.stage1_status },
    { label: 'Core graph', status: paper.stage2_status },
    { label: 'Deep graph', status: paper.stage3_status },
  ];
}

function sortedPapers(papers: PaperSummary[]): PaperSummary[] {
  return [...papers].sort((left, right) => (
    left.original_filename.localeCompare(right.original_filename)
    || left.id.localeCompare(right.id)
  ));
}

function mergePapers(
  existing: PaperSummary[],
  incoming: PaperSummary[],
): PaperSummary[] {
  const byId = new Map(existing.map((paper) => [paper.id, paper]));
  for (const paper of incoming) {
    byId.set(paper.id, paper);
  }
  return sortedPapers([...byId.values()]);
}

function publicError(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function paperStageSummary(paper: PaperSummary): string {
  return stagesFor(paper)
    .map((stage) => `${stage.label} ${statusLabel(stage.status).toLowerCase()}`)
    .join(', ');
}

export function PaperLibrary({ activePaperId, paperUpdate, onPaperSelected }: PaperLibraryProps) {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const uploadInFlight = useRef(false);

  useEffect(() => {
    let active = true;

    void paperApi.listPapers()
      .then((listedPapers) => {
        if (active) {
          setPapers((current) => mergePapers(listedPapers, current));
          setListError(null);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setListError(publicError(error, 'Unable to load the paper library.'));
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (paperUpdate !== null) {
      setPapers((current) => mergePapers(current, [paperUpdate]));
    }
  }, [paperUpdate]);

  const upload = async (file: File) => {
    if (uploadInFlight.current) {
      return;
    }

    uploadInFlight.current = true;
    setUploadingFilename(file.name);
    setUploadError(null);

    try {
      const paper = await paperApi.upload(file);
      setPapers((current) => mergePapers(current, [paper]));
      onPaperSelected(paper);
    } catch (error) {
      setUploadError(publicError(error, 'Unable to upload this PDF.'));
    } finally {
      uploadInFlight.current = false;
      setUploadingFilename(null);
    }
  };

  return (
    <aside className="paper-library" aria-label="Paper library">
      <header className="paper-library__header">
        <p className="paper-library__eyebrow">Local research desk</p>
        <h2>Paper library</h2>
        <p>Read source evidence, graph connections, and research notes together.</p>
      </header>

      <div className="paper-library__upload">
        <label htmlFor="paper-upload">Upload PDF</label>
        <input
          id="paper-upload"
          type="file"
          accept="application/pdf,.pdf"
          disabled={uploadingFilename !== null}
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = '';
            if (file !== undefined) {
              void upload(file);
            }
          }}
        />
        <p className="paper-library__upload-help">One PDF can be processed at a time.</p>
      </div>

      {loading && (
        <p className="paper-library__status" role="status">Loading paper library…</p>
      )}
      {uploadingFilename !== null && (
        <p className="paper-library__status" role="status">
          Uploading {uploadingFilename}…
        </p>
      )}
      {listError !== null && <p className="paper-library__error" role="alert">{listError}</p>}
      {uploadError !== null && <p className="paper-library__error" role="alert">{uploadError}</p>}

      {!loading && listError === null && papers.length === 0 ? (
        <p className="paper-library__empty">No papers in the library yet.</p>
      ) : null}

      {papers.length > 0 && (
        <nav className="paper-library__papers" aria-label="Available papers">
          {papers.map((paper) => (
            <button
              key={paper.id}
              type="button"
              className="paper-library__paper"
              aria-current={activePaperId === paper.id ? 'page' : undefined}
              onClick={() => onPaperSelected(paper)}
            >
              <span className="paper-library__filename">{paper.original_filename}</span>
              <span className="paper-library__stages">
                {stagesFor(paper).map((stage) => (
                  <span className="paper-library__stage" key={stage.label}>
                    <span>{stage.label} </span>
                    <strong>{statusLabel(stage.status)}</strong>
                  </span>
                ))}
              </span>
            </button>
          ))}
        </nav>
      )}
    </aside>
  );
}
