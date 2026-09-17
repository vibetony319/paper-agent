import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { PDFDocumentProxy } from 'pdfjs-dist';

type PdfLinkPreviewProps = {
  anchorRect: DOMRect;
  document: PDFDocumentProxy;
  /** Destination page of an internal link; null for external links. */
  pageNumber: number | null;
  /** External URL to show as text; null for internal links. */
  url: string | null;
};

const PREVIEW_WIDTH = 220;
const PREVIEW_CACHE_LIMIT = 8;
const VIEWPORT_MARGIN = 8;

// Thumbnails are cheap to re-render but hover repeats are frequent, so keep a
// small FIFO of already-rendered destination pages per document.
const previewCache = new Map<string, HTMLCanvasElement>();

function cacheKey(document: PDFDocumentProxy, pageNumber: number): string {
  const docId = (document as { docId?: string }).docId ?? 'doc';
  return `${docId}:${pageNumber}`;
}

export function PdfLinkPreview({ anchorRect, document, pageNumber, url }: PdfLinkPreviewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [thumbnailReady, setThumbnailReady] = useState(false);

  useLayoutEffect(() => {
    setThumbnailReady(false);
  }, [pageNumber]);

  useLayoutEffect(() => {
    const reposition = () => {
      const container = containerRef.current;
      if (container === null) return;
      const bounds = container.getBoundingClientRect();
      // Prefer above the link; fall back to below when clipped.
      let top = anchorRect.top - bounds.height - VIEWPORT_MARGIN;
      if (top < VIEWPORT_MARGIN) top = anchorRect.bottom + VIEWPORT_MARGIN;
      top = Math.min(
        Math.max(VIEWPORT_MARGIN, top),
        Math.max(VIEWPORT_MARGIN, window.innerHeight - bounds.height - VIEWPORT_MARGIN),
      );
      const left = Math.min(
        Math.max(
          VIEWPORT_MARGIN,
          anchorRect.left + anchorRect.width / 2 - bounds.width / 2,
        ),
        Math.max(VIEWPORT_MARGIN, window.innerWidth - bounds.width - VIEWPORT_MARGIN),
      );
      setPosition({ left, top });
    };
    reposition();
    window.addEventListener('resize', reposition);
    return () => window.removeEventListener('resize', reposition);
  }, [anchorRect, thumbnailReady]);

  useEffect(() => {
    if (pageNumber === null) return undefined;
    let cancelled = false;
    const paint = async () => {
      const key = cacheKey(document, pageNumber);
      let source = previewCache.get(key);
      try {
        if (source === undefined) {
          const page = await document.getPage(pageNumber);
          const base = page.getViewport({ scale: 1 });
          const scale = base.width > 0 ? PREVIEW_WIDTH / base.width : 1;
          const viewport = page.getViewport({ scale });
          source = globalThis.document.createElement('canvas');
          source.width = Math.max(1, Math.floor(viewport.width));
          source.height = Math.max(1, Math.floor(viewport.height));
          const context = source.getContext('2d');
          if (context !== null) {
            await page.render({
              canvas: source,
              canvasContext: context,
              viewport,
            }).promise;
          }
          if (previewCache.size >= PREVIEW_CACHE_LIMIT) {
            const oldest = previewCache.keys().next().value;
            if (oldest !== undefined) previewCache.delete(oldest);
          }
          previewCache.set(key, source);
        }
      } catch {
        // A failed thumbnail is non-fatal: the destination label still shows.
        return;
      }
      if (cancelled) return;
      const canvas = canvasRef.current;
      if (canvas === null || source === undefined) return;
      canvas.width = source.width;
      canvas.height = source.height;
      canvas.getContext('2d')?.drawImage(source, 0, 0);
      setThumbnailReady(true);
    };
    void paint();
    return () => {
      cancelled = true;
    };
  }, [document, pageNumber]);

  return createPortal(
    <div
      ref={containerRef}
      className="pdf-link-preview"
      role="tooltip"
      data-testid="pdf-link-preview"
      style={position ?? undefined}
    >
      {pageNumber !== null ? (
        <>
          <p className="pdf-link-preview__label">跳转到第 {pageNumber} 页</p>
          <canvas ref={canvasRef} className="pdf-link-preview__canvas" aria-hidden="true" />
        </>
      ) : (
        <p className="pdf-link-preview__url">{url}</p>
      )}
    </div>,
    // The prop shadows the global document; the portal target is the page body.
    globalThis.document.body,
  );
}
