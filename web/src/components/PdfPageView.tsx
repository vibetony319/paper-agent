import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist';

import type { Page } from '../api/types';
import { TextLayer } from '../pdfjs';
import type { SourceTarget } from '../workspace/types';
import { sourceOverlayStyle } from './pdfGeometry';

type PdfPageViewProps = {
  document: PDFDocumentProxy;
  page: Page;
  active: boolean;
  overlays: SourceTarget[];
};

function isRenderCancellation(error: unknown): boolean {
  return error instanceof Error && error.name === 'RenderingCancelledException';
}

function pageShellFor(surface: HTMLElement): HTMLElement | null {
  return surface.closest('.pdf-reader__page-shell');
}

export function PdfPageView({ document, page, active, overlays }: PdfPageViewProps) {
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [hasSelectableText, setHasSelectableText] = useState(true);

  useEffect(() => {
    if (!active || typeof ResizeObserver === 'undefined') return undefined;
    const surface = surfaceRef.current;
    const target = surface === null ? null : pageShellFor(surface) ?? surface.parentElement;
    if (target === null || target === undefined) return undefined;

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width !== undefined && width > 0) setContainerWidth(width);
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
        const availableWidth = containerWidth ?? pageShellFor(surface)?.clientWidth ?? surface.clientWidth;
        const scale = availableWidth > 0 && availableWidth < baseViewport.width
          ? availableWidth / baseViewport.width
          : 1;
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
  }, [active, containerWidth, document, page.number]);

  return (
    <div className="pdf-page-view" data-pdf-page={page.number}>
      <div ref={surfaceRef} className="pdf-page-view__surface">
        <canvas ref={canvasRef} role="img" aria-label={`PDF 第 ${page.number} 页`} />
        <div ref={textLayerRef} className="pdf-page-view__text-layer" data-testid={`pdf-text-layer-${page.number}`} />
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
