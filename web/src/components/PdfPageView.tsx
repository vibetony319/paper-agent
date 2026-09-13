import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist';

import type { Highlight, HighlightColor, Page } from '../api/types';
import { TextLayer } from '../pdfjs';
import type { SourceTarget } from '../workspace/types';
import { sourceOverlayStyle } from './pdfGeometry';
import { AnnotationOverlay } from './AnnotationOverlay';

type PdfPageViewProps = {
  document: PDFDocumentProxy;
  page: Page;
  active: boolean;
  overlays: SourceTarget[];
  highlights?: Highlight[];
  zoom?: number;
  onHighlightNote?: (highlight: Highlight, rect: DOMRect) => void;
  onHighlightDeleted?: (highlightId: string) => void;
  onHighlightColorChange?: (highlightId: string, color: HighlightColor) => void;
};

function isRenderCancellation(error: unknown): boolean {
  return error instanceof Error && error.name === 'RenderingCancelledException';
}

function pageShellFor(surface: HTMLElement): HTMLElement | null {
  return surface.closest('.pdf-reader__page-shell');
}

function measurableContainer(surface: HTMLElement): HTMLElement | null {
  return surface.closest<HTMLElement>('.pdf-reader__pages')
    ?? pageShellFor(surface)
    ?? surface.parentElement;
}

function horizontalPaddingOf(element: HTMLElement | null): number {
  if (element === null) return 0;
  const styles = window.getComputedStyle(element);
  const padding = Number.parseFloat(styles.paddingLeft) + Number.parseFloat(styles.paddingRight);
  return Number.isFinite(padding) ? padding : 0;
}

function contentWidthOf(element: HTMLElement): number {
  const width = element.clientWidth - horizontalPaddingOf(element);
  return width > 0 ? width : 0;
}

export function PdfPageView({
  document, page, active, overlays, highlights = [], zoom = 1, onHighlightNote = () => undefined, onHighlightDeleted = () => undefined, onHighlightColorChange,
}: PdfPageViewProps) {
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [hasSelectableText, setHasSelectableText] = useState(true);

  useEffect(() => {
    if (!active || typeof ResizeObserver === 'undefined') return undefined;
    const surface = surfaceRef.current;
    if (surface === null) return undefined;
    const target = measurableContainer(surface);
    if (target === null || target === undefined) return undefined;

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry === undefined || entry.contentRect === undefined) return;
      const entryTarget = entry.target instanceof HTMLElement ? entry.target : null;
      // The pages container keeps a stable width while zoomed pages overflow it,
      // so derive the fit width from it and discount the page shell gutter.
      const width = entryTarget !== null && entryTarget.classList.contains('pdf-reader__pages')
        ? entry.contentRect.width - horizontalPaddingOf(pageShellFor(surface))
        : entry.contentRect.width;
      if (width > 0) setContainerWidth(width);
    });
    resizeObserver.observe(target);
    return () => resizeObserver.disconnect();
  }, [active]);

  useEffect(() => {
    if (!active) {
      return undefined;
    }

    let mounted = true;
    let renderTask: RenderTask | undefined;
    let textLayer: InstanceType<typeof TextLayer> | undefined;

    const renderPage = async () => {
      try {
        const pdfPage: PDFPageProxy = await document.getPage(page.number);
        if (!mounted) return;

        const canvas = canvasRef.current;
        const textContainer = textLayerRef.current;
        const surface = surfaceRef.current;
        if (canvas === null || textContainer === null || surface === null) return;

        const baseViewport = pdfPage.getViewport({ scale: 1 });
        const target = measurableContainer(surface);
        const availableWidth = containerWidth ?? (target === null ? 0 : contentWidthOf(target));
        const fitScale = availableWidth > 0 && availableWidth < baseViewport.width
          ? availableWidth / baseViewport.width
          : 1;
        const scale = fitScale * zoom;
        const viewport = pdfPage.getViewport({ scale });
        const pixelRatio = window.devicePixelRatio || 1;
        const context = canvas.getContext('2d');
        if (context === null) throw new Error('Canvas rendering is unavailable.');

        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        surface.style.width = `${viewport.width}px`;
        surface.style.height = `${viewport.height}px`;
        textContainer.replaceChildren();
        textContainer.style.width = `${viewport.width}px`;
        textContainer.style.height = `${viewport.height}px`;
        textContainer.style.setProperty(
          '--total-scale-factor',
          String(viewport.scale * viewport.userUnit),
        );
        textContainer.style.setProperty('--scale-round-x', '1px');
        textContainer.style.setProperty('--scale-round-y', '1px');

        renderTask = pdfPage.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        textLayer = new TextLayer({
          textContentSource: pdfPage.streamTextContent(),
          container: textContainer,
          viewport,
        });
        await Promise.all([renderTask.promise, textLayer.render()]);
        if (!mounted) return;

        setErrorMessage(null);
        setHasSelectableText(
          (textLayer.textContentItemsStr?.length ?? textContainer.childElementCount) > 0,
        );

      } catch (error) {
        if (mounted && !isRenderCancellation(error)) {
          setErrorMessage('该页暂时无法显示。');
        }
      }
    };

    setErrorMessage(null);
    setHasSelectableText(true);
    void renderPage();

    return () => {
      mounted = false;
      textLayer?.cancel();
      renderTask?.cancel();
    };
  }, [active, containerWidth, document, page.number, zoom]);

  return (
    <div className="pdf-page-view" data-pdf-page={page.number} tabIndex={-1}>
      <div ref={surfaceRef} className="pdf-page-view__surface">
        <canvas ref={canvasRef} role="img" aria-label={`PDF 第 ${page.number} 页`} />
        <div ref={textLayerRef} className="pdf-page-view__text-layer" data-testid={`pdf-text-layer-${page.number}`} />
        <AnnotationOverlay
          highlights={highlights}
          onAddNote={onHighlightNote}
          onDeleteHighlight={onHighlightDeleted}
          onChangeColor={onHighlightColorChange}
        />
        {overlays.map((overlay) => (
          <div
            key={overlay.id}
            className="pdf-reader__overlay"
            data-testid={`source-overlay-${page.number}`}
            style={sourceOverlayStyle(overlay) ?? undefined}
            aria-label="当前证据位置"
          />
        ))}
      </div>
      {!hasSelectableText ? <p className="pdf-page-view__no-text">该页无法选择文字</p> : null}
      {errorMessage !== null ? <p className="pdf-reader__error" role="alert">{errorMessage}</p> : null}
    </div>
  );
}
