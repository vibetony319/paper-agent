import { useEffect, useMemo, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';

import { paperApi } from '../api/client';
import type { Page } from '../api/types';
import { getDocument } from '../pdfjs';
import type { SourceTarget } from '../workspace/types';
import { PdfPageView } from './PdfPageView';

type PdfReaderProps = {
  paperId: string;
  pages: Page[];
  activeSource: SourceTarget | null;
  onSourceCleared: () => void;
};

type ReaderStatus = 'loading' | 'ready' | 'error';

function sortedPages(pages: Page[]): Page[] {
  return [...pages].sort((left, right) => left.number - right.number);
}

function addOverscan(pageNumbers: number[], activePages: Set<number>): Set<number> {
  const result = new Set(activePages);
  for (const pageNumber of activePages) {
    const index = pageNumbers.indexOf(pageNumber);
    if (index > 0) result.add(pageNumbers[index - 1]);
    if (index >= 0 && index < pageNumbers.length - 1) result.add(pageNumbers[index + 1]);
  }
  return result;
}

export function PdfReader({ paperId, pages, activeSource, onSourceCleared }: PdfReaderProps) {
  const orderedPages = useMemo(() => sortedPages(pages), [pages]);
  const pageNumbers = useMemo(() => orderedPages.map((page) => page.number), [orderedPages]);
  const pageSignature = pageNumbers.join(',');
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [status, setStatus] = useState<ReaderStatus>('loading');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [visiblePages, setVisiblePages] = useState<Set<number>>(() => new Set(pageNumbers.slice(0, 1)));
  const pageShells = useRef(new Map<number, HTMLDivElement>());

  useEffect(() => {
    setVisiblePages(new Set(pageNumbers.slice(0, 1)));
  }, [pageSignature, paperId]);

  useEffect(() => {
    if (pages.length === 0) {
      setDocument(null);
      setStatus('ready');
      setErrorMessage(null);
      return undefined;
    }

    let mounted = true;
    const loadingTask = getDocument({ url: paperApi.getSourceUrl(paperId) });
    setDocument(null);
    setStatus('loading');
    setErrorMessage(null);

    void loadingTask.promise.then(
      (pdfDocument) => {
        if (!mounted) return;
        setDocument(pdfDocument);
        setStatus('ready');
      },
      () => {
        if (!mounted) return;
        setStatus('error');
        setErrorMessage('原始 PDF 暂时无法加载。');
      },
    );

    return () => {
      mounted = false;
      void loadingTask.destroy().catch(() => undefined);
    };
  }, [paperId]);

  useEffect(() => {
    if (orderedPages.length === 0 || typeof IntersectionObserver === 'undefined') {
      return undefined;
    }

    const observer = new IntersectionObserver((entries) => {
      setVisiblePages((current) => {
        const next = new Set(current);
        for (const entry of entries) {
          const pageNumber = Number((entry.target as HTMLElement).dataset.pdfPageNumber);
          if (!Number.isInteger(pageNumber)) continue;
          if (entry.isIntersecting) next.add(pageNumber);
          else next.delete(pageNumber);
        }
        return next;
      });
    }, { rootMargin: '1200px 0px' });

    for (const shell of pageShells.current.values()) observer.observe(shell);
    return () => observer.disconnect();
  }, [pageSignature, orderedPages.length]);

  useEffect(() => {
    if (activeSource === null || !pageNumbers.includes(activeSource.pageNumber)) return;
    const target = pageShells.current.get(activeSource.pageNumber);
    if (target === undefined) return;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({ block: 'center', behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [activeSource, pageNumbers]);

  const forcedPages = activeSource === null ? new Set<number>() : new Set([activeSource.pageNumber]);
  const activePages = addOverscan(pageNumbers, new Set([...visiblePages, ...forcedPages]));

  return (
    <section className="pdf-reader" aria-label="论文阅读器">
      <header className="pdf-reader__header">
        <div>
          <p className="pdf-reader__eyebrow">原始论文</p>
          <h2>PDF 阅读</h2>
        </div>
        <a
          className="pdf-reader__source-link"
          href={paperApi.getSourceUrl(paperId)}
          target="_blank"
          rel="noreferrer"
        >
          打开原始 PDF
        </a>
      </header>

      {activeSource !== null ? (
        <div className="pdf-reader__controls">
          <span>已定位到第 {activeSource.pageNumber} 页证据</span>
          <button type="button" onClick={onSourceCleared}>清除证据定位</button>
        </div>
      ) : null}

      {orderedPages.length === 0 ? (
        <p className="pdf-reader__empty">这篇论文没有可用的页面。</p>
      ) : (
        <div className="pdf-reader__pages" aria-busy={status === 'loading'}>
          {status === 'loading' ? <p className="pdf-reader__status" role="status">正在加载原始 PDF…</p> : null}
          {status === 'error' ? <p className="pdf-reader__error" role="alert">{errorMessage}</p> : null}
          {orderedPages.map((page) => {
            const isActive = activePages.has(page.number);
            const overlays = activeSource?.pageNumber === page.number ? [activeSource] : [];
            return (
              <div
                key={page.id}
                ref={(element) => {
                  if (element === null) pageShells.current.delete(page.number);
                  else pageShells.current.set(page.number, element);
                }}
                className="pdf-reader__page-shell"
                data-testid={`pdf-page-shell-${page.number}`}
                data-pdf-page-number={page.number}
                style={{ aspectRatio: `${page.width} / ${page.height}` }}
              >
                {document !== null && isActive ? (
                  <PdfPageView document={document} page={page} active overlays={overlays} />
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
