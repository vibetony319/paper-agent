import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { PDFDocumentProxy, PDFPageProxy, PageViewport } from 'pdfjs-dist';

import type { BoundingBox } from '../api/types';
import { PdfLinkPreview } from './PdfLinkPreview';

/** Where an internal PDF link points: a page plus an optional band on it. */
export interface PdfLinkTarget {
  pageNumber: number;
  bbox: BoundingBox | null;
  /** Point destinations need their nearby text line resolved after rendering. */
  pointDestination: boolean;
}

type PdfLinkLayerProps = {
  document: PDFDocumentProxy;
  pdfPage: PDFPageProxy;
  viewport: PageViewport;
  pageNumber: number;
  onNavigate?: (target: PdfLinkTarget) => void;
};

interface PdfLinkAnnotation {
  subtype?: string;
  rect?: number[];
  url?: string;
  unsafeUrl?: string;
  dest?: string | unknown[];
}

type InternalLink = { kind: 'internal'; target: PdfLinkTarget };
type ExternalLink = { kind: 'external'; url: string };
type ResolvedLink = (InternalLink | ExternalLink) & { title: string; rect: number[] };

const HOVER_DELAY_MS = 200;
const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function externalUrl(annotation: PdfLinkAnnotation): string | null {
  const raw = annotation.url ?? annotation.unsafeUrl;
  if (typeof raw !== 'string') return null;
  try {
    const parsed = new URL(raw);
    return SAFE_PROTOCOLS.has(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

async function resolveDest(
  document: PDFDocumentProxy,
  dest: string | unknown[],
): Promise<PdfLinkTarget | null> {
  let explicit: unknown[] | null = null;
  if (typeof dest === 'string') {
    try {
      explicit = await document.getDestination(dest);
    } catch {
      return null;
    }
  } else if (Array.isArray(dest)) {
    explicit = dest;
  }
  if (explicit === null || explicit.length === 0) return null;

  const reference = explicit[0];
  let pageNumber: number | null = null;
  if (typeof reference === 'number' && Number.isInteger(reference)) {
    pageNumber = reference + 1;
  } else if (typeof reference === 'object' && reference !== null) {
    try {
      pageNumber = (await document.getPageIndex(reference as never)) + 1;
    } catch {
      return null;
    }
  }
  if (pageNumber === null) return null;

  // Destinations use PDF user coordinates. Convert them through PDF.js's
  // viewport so crop boxes and page rotation match the rendered surface.
  const destinationType = typeof explicit[1] === 'object' && explicit[1] !== null
    ? ((explicit[1] as { name?: string; type?: string }).name ?? (explicit[1] as { type?: string }).type)
    : null;
  const left = typeof explicit[2] === 'number' && Number.isFinite(explicit[2]) ? explicit[2] : null;
  const top = typeof explicit[3] === 'number' && Number.isFinite(explicit[3]) ? explicit[3] : null;
  let bbox: BoundingBox | null = null;
  let pointDestination = false;
  if ((destinationType === 'XYZ' || destinationType === 'FitH' || destinationType === 'FitBH' || destinationType === 'FitV' || destinationType === 'FitBV') && (left !== null || top !== null)) {
    pointDestination = true;
    try {
      const page = await document.getPage(pageNumber);
      const viewport = page.getViewport({ scale: 1 });
      const [viewX0, , , viewY1] = page.view as [number, number, number, number];
      const x = destinationType === 'FitH' || destinationType === 'FitBH' ? viewX0 : left ?? viewX0;
      const y = destinationType === 'FitV' || destinationType === 'FitBV' ? viewY1
        : destinationType === 'FitH' || destinationType === 'FitBH' ? left ?? viewY1
          : top ?? viewY1;
      const [pointX, pointY] = viewport.convertToViewportPoint(x, y);
      const normalizedLeft = clamp01(pointX / viewport.width);
      const normalizedTop = clamp01(pointY / viewport.height);
      const markerWidth = Math.min(0.025, 16 / viewport.width);
      const markerHeight = Math.min(0.02, 16 / viewport.height);
      bbox = {
        x0: Math.max(0, normalizedLeft - markerWidth / 2),
        y0: Math.max(0, normalizedTop - markerHeight / 2),
        x1: Math.min(1, normalizedLeft + markerWidth / 2),
        y1: Math.min(1, normalizedTop + markerHeight / 2),
      };
    } catch {
      bbox = null;
    }
  } else if (destinationType === 'FitR' && explicit.length >= 6 && explicit.slice(2, 6).every((value) => typeof value === 'number' && Number.isFinite(value))) {
    try {
      const page = await document.getPage(pageNumber);
      const viewport = page.getViewport({ scale: 1 });
      const [firstX, firstY] = viewport.convertToViewportPoint(explicit[2] as number, explicit[3] as number);
      const [secondX, secondY] = viewport.convertToViewportPoint(explicit[4] as number, explicit[5] as number);
      bbox = {
        x0: clamp01(Math.min(firstX, secondX) / viewport.width),
        y0: clamp01(Math.min(firstY, secondY) / viewport.height),
        x1: clamp01(Math.max(firstX, secondX) / viewport.width),
        y1: clamp01(Math.max(firstY, secondY) / viewport.height),
      };
      if (bbox.x0 >= bbox.x1 || bbox.y0 >= bbox.y1) bbox = null;
    } catch {
      bbox = null;
    }
  }
  return { pageNumber, bbox, pointDestination };
}

function hitboxStyle(rect: number[], viewport: PageViewport): CSSProperties {
  const [x1, y1] = viewport.convertToViewportPoint(rect[0], rect[1]);
  const [x2, y2] = viewport.convertToViewportPoint(rect[2], rect[3]);
  return {
    left: `${Math.min(x1, x2)}px`,
    top: `${Math.min(y1, y2)}px`,
    width: `${Math.max(Math.abs(x2 - x1), 2)}px`,
    height: `${Math.max(Math.abs(y2 - y1), 6)}px`,
  };
}

export function PdfLinkLayer({
  document,
  pdfPage,
  viewport,
  pageNumber,
  onNavigate,
}: PdfLinkLayerProps) {
  const [links, setLinks] = useState<ResolvedLink[]>([]);
  const [hovered, setHovered] = useState<{ link: ResolvedLink; rect: DOMRect } | null>(null);
  const hoverTimer = useRef<number | null>(null);

  useEffect(() => {
    let mounted = true;
    setLinks([]);
    const resolve = async () => {
      let annotations: PdfLinkAnnotation[];
      try {
        annotations = (await pdfPage.getAnnotations({ intent: 'display' })) as PdfLinkAnnotation[];
      } catch {
        return;
      }
      if (!mounted) return;
      const resolved: ResolvedLink[] = [];
      for (const annotation of annotations) {
        if (
          annotation.subtype !== 'Link'
          || !Array.isArray(annotation.rect)
          || annotation.rect.length !== 4
        ) {
          continue;
        }
        const url = externalUrl(annotation);
        if (url !== null) {
          resolved.push({ kind: 'external', url, title: url, rect: annotation.rect });
          continue;
        }
        if (annotation.dest === undefined) continue;
        const target = await resolveDest(document, annotation.dest);
        if (target === null) continue;
        resolved.push({
          kind: 'internal',
          target,
          title: `跳转到第 ${target.pageNumber} 页`,
          rect: annotation.rect,
        });
      }
      if (mounted) setLinks(resolved);
    };
    void resolve();
    return () => {
      mounted = false;
    };
  }, [document, pdfPage]);

  const startHover = (link: ResolvedLink, element: HTMLElement) => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => {
      hoverTimer.current = null;
      setHovered({ link, rect: element.getBoundingClientRect() });
    }, HOVER_DELAY_MS);
  };

  const stopHover = () => {
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
    setHovered(null);
  };

  useEffect(
    () => () => {
      if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    },
    [],
  );

  return (
    <div className="pdf-page-view__link-layer" data-testid={`pdf-link-layer-${pageNumber}`}>
      {links.map((link, index) => {
        const style = hitboxStyle(link.rect, viewport);
        if (link.kind === 'external') {
          return (
            <a
              key={index}
              className="pdf-link-hitbox"
              data-testid="pdf-link-external"
              style={style}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              title={link.title}
              aria-label={`打开外部链接：${link.title}`}
              onMouseEnter={(event) => startHover(link, event.currentTarget)}
              onMouseLeave={stopHover}
            />
          );
        }
        return (
          <button
            key={index}
            type="button"
            className="pdf-link-hitbox"
            data-testid="pdf-link-internal"
            style={style}
            title={link.title}
            aria-label={link.title}
            onClick={() => onNavigate?.(link.target)}
            onMouseEnter={(event) => startHover(link, event.currentTarget)}
            onMouseLeave={stopHover}
            onFocus={(event) => startHover(link, event.currentTarget)}
            onBlur={stopHover}
          />
        );
      })}
      {hovered !== null ? (
        <PdfLinkPreview
          anchorRect={hovered.rect}
          document={document}
          pageNumber={hovered.link.kind === 'internal' ? hovered.link.target.pageNumber : null}
          url={hovered.link.kind === 'external' ? hovered.link.url : null}
        />
      ) : null}
    </div>
  );
}
