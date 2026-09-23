import { useEffect, useRef, useState } from 'react';

import { ApiError, paperApi } from '../api/client';
import type { PaperSummary, ProcessingStatus } from '../api/types';

export interface PaperLibraryProps {
  activePaperId: string | null;
  paperUpdate: PaperSummary | null;
  onPaperSelected: (paper: PaperSummary) => void;
  onPaperDeleteRequested: (paper: PaperSummary) => void;
  focusHeading: boolean;
}

type Stage = {
  label: string;
  status: ProcessingStatus | null;
};

function statusLabel(status: ProcessingStatus | null): string {
  switch (status) {
    case 'completed':
      return '已完成';
    case 'queued':
      return '等待中';
    case 'running':
      return '处理中';
    case 'partial':
      return '部分完成';
    case 'failed':
      return '失败';
    case null:
      return '未开始';
  }
}

function stagesFor(paper: PaperSummary): Stage[] {
  return [
    { label: '页面定位', status: paper.stage0_status },
    { label: '结构解析', status: paper.stage1_status },
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

export function PaperLibrary({
  activePaperId,
  paperUpdate,
  onPaperSelected,
  onPaperDeleteRequested,
  focusHeading,
}: PaperLibraryProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const uploadInFlight = useRef(false);

  useEffect(() => {
    if (focusHeading) {
      headingRef.current?.focus();
    }
  }, [focusHeading]);

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
          setListError(publicError(error, '无法加载论文库。'));
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
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      setUploadError('请选择 PDF 文件。');
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
      setUploadError(publicError(error, '无法上传此 PDF。'));
    } finally {
      uploadInFlight.current = false;
      setUploadingFilename(null);
    }
  };

  return (
    <section className="paper-library" aria-labelledby="paper-library-title">
      <header className="paper-library__header">
        <p className="paper-library__eyebrow">本地研究工作台</p>
        <h1 id="paper-library-title" ref={headingRef} tabIndex={-1}>论文库</h1>
        <p>在一个本地阅读视图中查看原始页面、证据关联和研究笔记。</p>
      </header>

      <div className={`paper-library__upload${dragging ? ' paper-library__upload--dragging' : ''}`}
        onDragEnter={(event) => { if (!event.dataTransfer.types.includes('Files')) return; event.preventDefault(); dragDepth.current += 1; setDragging(true); }}
        onDragOver={(event) => { if (!event.dataTransfer.types.includes('Files')) return; event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
        onDragLeave={(event) => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDragging(false); }}
        onDrop={(event) => { event.preventDefault(); dragDepth.current = 0; setDragging(false); const file = event.dataTransfer.files[0]; if (file !== undefined) void upload(file); }}
      >
        <label htmlFor="paper-upload">上传 PDF</label>
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
        <p className="paper-library__upload-help">也可以将 PDF 拖到这里，一次处理一篇。</p>
      </div>

      {loading && (
        <p className="paper-library__status" role="status">正在加载论文库…</p>
      )}
      {uploadingFilename !== null && (
        <p className="paper-library__status" role="status">
          正在上传 {uploadingFilename}…
        </p>
      )}
      {listError !== null && <p className="paper-library__error" role="alert">{listError}</p>}
      {uploadError !== null && <p className="paper-library__error" role="alert">{uploadError}</p>}

      {!loading && listError === null && papers.length === 0 ? (
        <p className="paper-library__empty">论文库中还没有论文。</p>
      ) : null}

      {papers.length > 0 && (
        <nav className="paper-library__papers" aria-label="可用论文">
          {papers.map((paper) => (
            <article
              key={paper.id}
              className="paper-library__paper"
            >
              <button
                type="button"
                className="paper-library__open"
                aria-current={activePaperId === paper.id ? 'page' : undefined}
                aria-label={`打开 ${paper.original_filename}`}
                onClick={() => onPaperSelected(paper)}
              >
                <span className="paper-library__filename">{paper.original_filename}</span>
                <span className="paper-library__stages">
                  {stagesFor(paper).map((stage) => (
                    <span className="paper-library__stage" key={stage.label} data-status={stage.status ?? 'none'}>
                      <span>{stage.label} </span>
                      <strong>{statusLabel(stage.status)}</strong>
                    </span>
                  ))}
                </span>
              </button>
              <button
                className="paper-library__delete"
                type="button"
                aria-label={`删除 ${paper.original_filename}`}
                onClick={() => onPaperDeleteRequested(paper)}
              >
                删除
              </button>
            </article>
          ))}
        </nav>
      )}
    </section>
  );
}
