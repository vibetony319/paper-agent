import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { PDFDocumentProxy, PDFPageProxy, PageViewport } from 'pdfjs-dist';

import type { BoundingBox } from '../api/types';
import { PdfLinkPreview } from './PdfLinkPreview';

/** Where an internal PDF link points: a page plus an optional band on it. */
export interface PdfLinkTarget {
  pageNumber: number;
  bbox: BoundingBox | null;
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

  // Explicit destinations are [page, type, left, top, zoom] in PDF user
  // space (origin bottom-left). Synthesize a highlight band at the target.
  const left = typeof explicit[2] === 'number' ? explicit[2] : null;
  const top = typeof explicit[3] === 'number' ? explicit[3] : null;
  let bbox: BoundingBox | null = null;
  if (left !== null || top !== null) {
    try {
      const page = await document.getPage(pageNumber);
      const [x0, y0, x1, y1] = page.view as [number, number, number, number];
      const width = Math.max(x1 - x0, 1);
      const height = Math.max(y1 - y0, 1);
      const normalizedLeft = left === null ? 0 : clamp01((left - x0) / width);
      const normalizedTop = top === null ? 0 : clamp01((y1 - top) / height);
      bbox = {
        x0: left === null ? 0 : Math.max(0, normalizedLeft - 0.02),
        y0: Math.max(0, normalizedTop - 0.06),
        x1: left === null ? 1 : Math.min(1, normalizedLeft + 0.3),
        y1: Math.min(1, normalizedTop + 0.02),
      };
    } catch {
      bbox = null;
    }
  }
  return { pageNumber, bbox };
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
