import { useEffect, useMemo, useRef, useState } from 'react';
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist';
import type { RenderTask } from 'pdfjs-dist';

import { paperApi } from '../api/client';
import type { Page } from '../api/types';
import type { SourceTarget } from '../workspace/types';
import { sourceOverlayStyle } from './pdfGeometry';

GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

type PdfReaderProps = {
  paperId: string;
  pages: Page[];
  activeSource: SourceTarget | null;
  onSourceCleared: () => void;
};

type ReaderStatus = 'loading' | 'ready' | 'error';

function sortedPageNumbers(pages: Page[]): number[] {
  return [...new Set(pages.map((page) => page.number))].sort((left, right) => left - right);
}

function closestAvailablePage(pageNumbers: number[], requestedPage: number): number {
  return pageNumbers.reduce((closest, pageNumber) => (
    Math.abs(pageNumber - requestedPage) < Math.abs(closest - requestedPage)
      ? pageNumber
      : closest
  ));
}

function preferredPage(pageNumbers: number[], activeSource: SourceTarget | null): number | null {
  if (pageNumbers.length === 0) {
    return null;
  }

  if (activeSource !== null && pageNumbers.includes(activeSource.pageNumber)) {
    return activeSource.pageNumber;
  }

  return pageNumbers[0];
}

function isRenderCancellation(error: unknown): boolean {
  return error instanceof Error && error.name === 'RenderingCancelledException';
}

export function PdfReader({
  paperId,
  pages,
  activeSource,
  onSourceCleared,
}: PdfReaderProps) {
  const pageNumbers = useMemo(() => sortedPageNumbers(pages), [pages]);
  const pageSignature = pageNumbers.join(',');
  const [pageNumber, setPageNumber] = useState<number | null>(() => preferredPage(pageNumbers, activeSource));
  const [status, setStatus] = useState<ReaderStatus>('loading');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previousPaperId = useRef(paperId);

  useEffect(() => {
    const nextPage = preferredPage(pageNumbers, activeSource);

    if (previousPaperId.current !== paperId) {
      previousPaperId.current = paperId;
      setPageNumber(nextPage);
      return;
    }

    if (activeSource !== null && pageNumbers.includes(activeSource.pageNumber)) {
      setPageNumber(activeSource.pageNumber);
      return;
    }

    setPageNumber((currentPage) => (
      currentPage !== null && pageNumbers.includes(currentPage) ? currentPage : nextPage
    ));
  }, [activeSource, pageNumbers, pageSignature, paperId]);

  useEffect(() => {
    if (pageNumber === null) {
      setStatus('ready');
      setErrorMessage(null);
      return undefined;
    }

    let active = true;
    let renderTask: RenderTask | undefined;
    const loadingTask = getDocument({ url: paperApi.getSourceUrl(paperId) });

    setStatus('loading');
    setErrorMessage(null);

    const renderPage = async () => {
      try {
        const document = await loadingTask.promise;
        if (!active) {
          return;
        }

        const page = await document.getPage(pageNumber);
        if (!active) {
          return;
        }

        const canvas = canvasRef.current;
        const context = canvas?.getContext('2d');
        if (canvas === null) {
          return;
        }
        if (context === null) {
          throw new Error('Canvas rendering is unavailable.');
        }

        const viewport = page.getViewport({ scale: 1.25 });
        const pixelRatio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;

        renderTask = page.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        await renderTask.promise;

        if (active) {
          setStatus('ready');
        }
      } catch (error) {
        if (active && !isRenderCancellation(error)) {
          setStatus('error');
          setErrorMessage('Unable to render the original PDF.');
        }
      }
    };

    void renderPage();

    return () => {
      active = false;
      renderTask?.cancel();
      void loadingTask.destroy().catch(() => undefined);
    };
  }, [pageNumber, paperId]);

  const currentPageIndex = pageNumber === null ? -1 : pageNumbers.indexOf(pageNumber);
  const hasPreviousPage = currentPageIndex > 0;
  const hasNextPage = currentPageIndex >= 0 && currentPageIndex < pageNumbers.length - 1;
  const overlayStyle = activeSource?.pageNumber === pageNumber ? sourceOverlayStyle(activeSource) : null;
  const minimumPage = pageNumbers[0];
  const maximumPage = pageNumbers.at(-1);

  const selectPage = (requestedPage: number) => {
    if (pageNumbers.length > 0) {
      setPageNumber(closestAvailablePage(pageNumbers, requestedPage));
    }
  };

  return (
    <section className="pdf-reader" aria-label="Paper reader">
      <header className="pdf-reader__header">
        <div>
          <p className="pdf-reader__eyebrow">Source evidence</p>
          <h2>Original PDF</h2>
        </div>
        <a
          className="pdf-reader__source-link"
          href={paperApi.getSourceUrl(paperId)}
          target="_blank"
          rel="noreferrer"
        >
          Open original PDF
        </a>
      </header>

      {pageNumbers.length === 0 ? (
        <p className="pdf-reader__empty">No parsed pages are available for this paper.</p>
      ) : (
        <>
          <div className="pdf-reader__controls" aria-label="PDF pagination">
            <button
              type="button"
              onClick={() => setPageNumber(pageNumbers[currentPageIndex - 1])}
              disabled={!hasPreviousPage}
            >
              Previous page
            </button>
            <label>
              Page number
              <input
                aria-label="Page number"
                type="number"
                min={minimumPage}
                max={maximumPage}
                value={pageNumber ?? ''}
                onChange={(event) => {
                  const requestedPage = Number(event.currentTarget.value);
                  if (Number.isInteger(requestedPage)) {
                    selectPage(requestedPage);
                  }
                }}
              />
            </label>
            <span aria-live="polite">Page {pageNumber} of {pageNumbers.length}</span>
            <button
              type="button"
              onClick={() => setPageNumber(pageNumbers[currentPageIndex + 1])}
              disabled={!hasNextPage}
            >
              Next page
            </button>
            {overlayStyle !== null ? (
              <button type="button" onClick={onSourceCleared}>Clear evidence highlight</button>
            ) : null}
          </div>

          {status === 'loading' ? <p className="pdf-reader__status" role="status">Rendering page…</p> : null}
          {status === 'error' ? <p className="pdf-reader__error" role="alert">{errorMessage}</p> : null}

          <div className="pdf-reader__page" aria-busy={status === 'loading'}>
            <div className="pdf-reader__canvas-shell">
              <canvas
                key={`${paperId}-${pageNumber}`}
                ref={canvasRef}
                role="img"
                aria-label={`Rendered PDF page ${pageNumber}`}
              />
              {overlayStyle !== null ? (
                <div
                  className="pdf-reader__overlay"
                  data-testid="source-overlay"
                  style={overlayStyle}
                  aria-label={`Selected ${activeSource?.kind} evidence`}
                />
              ) : null}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
