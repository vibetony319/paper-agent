import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';

import { paperApi } from '../api/client';
import { newRequestId } from '../api/ids';
import type { Highlight, HighlightColor, Page, TextAnchorDraft } from '../api/types';
import { getDocument } from '../pdfjs';
import type { SourceTarget, WorkspaceState } from '../workspace/types';
import { PdfPageView } from './PdfPageView';
import type { PdfLinkTarget } from './PdfLinkLayer';
import { selectionToAnchorDraft } from './pdfSelection';
import { SelectionToolbar, type SelectionToolbarAction } from './SelectionToolbar';
import { InlineAssistantPopover } from './InlineAssistantPopover';
import { ManualNotePopover } from './ManualNotePopover';
import type { SelectionAssistAction } from '../api/types';

export interface SectionNavEntry {
  id: string;
  title: string;
  pageNumber: number | null;
  level?: number;
}

type PdfReaderProps = {
  paperId: string;
  pages: Page[];
  activeSource: SourceTarget | null;
  onSourceCleared: () => void;
  highlights?: Highlight[];
  selection?: WorkspaceState['selection'];
  onSelectionSet?: (draft: TextAnchorDraft, toolbarRect: DOMRect) => void;
  onSelectionClear?: () => void;
  onCreateHighlight?: () => void;
  onDeleteHighlight?: (highlightId: string) => void;
  onChangeHighlightColor?: (highlightId: string, color: HighlightColor) => void;
  sections?: SectionNavEntry[];
  sectionsTitle?: string;
  selectionActions?: Partial<Record<Exclude<SelectionToolbarAction, 'highlight'>, (draft: TextAnchorDraft) => void>>;
  selectedModelProfileId?: string | null;
  runSelectionAssist?: React.ComponentProps<typeof InlineAssistantPopover>['runSelectionAssist'];
  onCreateSelectionNote?: (body: string, draft: TextAnchorDraft) => Promise<unknown>;
};

type ReaderStatus = 'loading' | 'ready' | 'error';

type LinkJump = PdfLinkTarget & { id: string };
type ScrollPosition = { top: number; left: number };

const MAX_RETURN_POSITIONS = 16;
const LINK_TOAST_MS = 3_000;

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3] as const;
const ZOOM_EPSILON = 1e-9;
const MIN_ZOOM = ZOOM_STEPS[0];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];

function nextZoom(current: number, direction: 1 | -1): number {
  if (direction > 0) {
    return ZOOM_STEPS.find((step) => step > current + ZOOM_EPSILON) ?? current;
  }
  for (let index = ZOOM_STEPS.length - 1; index >= 0; index -= 1) {
    if (ZOOM_STEPS[index] < current - ZOOM_EPSILON) return ZOOM_STEPS[index];
  }
  return current;
}

function formatZoom(zoom: number): string {
  return `${Math.round(zoom * 100)}%`;
}

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

export function PdfReader({
  paperId,
  pages,
  activeSource,
  onSourceCleared,
  highlights = [],
  selection = null,
  onSelectionSet,
  onSelectionClear,
  onCreateHighlight,
  onDeleteHighlight,
  onChangeHighlightColor,
  sections = [],
  sectionsTitle = '章节导航',
  selectionActions,
  selectedModelProfileId = null,
  runSelectionAssist,
  onCreateSelectionNote,
}: PdfReaderProps) {
  const orderedPages = useMemo(() => sortedPages(pages), [pages]);
  const pageNumbers = useMemo(() => orderedPages.map((page) => page.number), [orderedPages]);
  const pageSignature = pageNumbers.join(',');
  const [pdfDocument, setPdfDocument] = useState<PDFDocumentProxy | null>(null);
  const [status, setStatus] = useState<ReaderStatus>('loading');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [visiblePages, setVisiblePages] = useState<Set<number>>(() => new Set(pageNumbers.slice(0, 1)));
  const [zoom, setZoom] = useState(1);
  const [assistTarget, setAssistTarget] = useState<{ action: SelectionAssistAction; draft: TextAnchorDraft; rect: DOMRect; model: string | null; id: string } | null>(null);
  const [manualNoteTarget, setManualNoteTarget] = useState<{ draft: TextAnchorDraft; rect: DOMRect } | null>(null);
  const [linkTarget, setLinkTarget] = useState<LinkJump | null>(null);
  const [returnStack, setReturnStack] = useState<ScrollPosition[]>([]);
  const [linkToast, setLinkToast] = useState<LinkJump | null>(null);
  const toastTimer = useRef<number | null>(null);
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const sectionsToggleRef = useRef<HTMLButtonElement | null>(null);
  const wasSectionsOpen = useRef(false);
  const pageShells = useRef(new Map<number, HTMLDivElement>());
  const readerRef = useRef<HTMLElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // The handle remounts only after the collapse renders, so restore focus then.
    if (wasSectionsOpen.current && !sectionsOpen) {
      sectionsToggleRef.current?.focus();
    }
    wasSectionsOpen.current = sectionsOpen;
  }, [sectionsOpen]);

  const scrollToPage = useCallback((pageNumber: number) => {
    const target = pageShells.current.get(pageNumber);
    if (target === undefined) return;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    target.scrollIntoView({ block: 'start', behavior: reducedMotion ? 'auto' : 'smooth' });
  }, []);

  const showLinkToast = useCallback((jump: LinkJump) => {
    setLinkToast(jump);
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setLinkToast(null), LINK_TOAST_MS);
  }, []);

  const handleLinkNavigate = useCallback((target: PdfLinkTarget) => {
    const scrollArea = scrollAreaRef.current;
    if (scrollArea !== null) {
      // Remember where the reader stood before following the link.
      setReturnStack((stack) => [
        ...stack.slice(-(MAX_RETURN_POSITIONS - 1)),
        { top: scrollArea.scrollTop, left: scrollArea.scrollLeft },
      ]);
    }
    const jump: LinkJump = { ...target, id: `link-${target.pageNumber}-${Date.now().toString(36)}` };
    setLinkTarget(jump);
    showLinkToast(jump);
    const shell = pageShells.current.get(target.pageNumber);
    if (shell === undefined || scrollArea === null) return;
    const shellRect = shell.getBoundingClientRect();
    const areaRect = scrollArea.getBoundingClientRect();
    // Land the destination band near the top of the viewport instead of
    // snapping the whole page; shells keep their aspect-ratio height even
    // before the virtualized page content renders.
    const offsetY = target.bbox !== null ? target.bbox.y0 * shellRect.height : 0;
    const top = Math.max(0, shellRect.top - areaRect.top + scrollArea.scrollTop + offsetY - 80);
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    scrollArea.scrollTo({ top, behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [showLinkToast]);

  const popReturnPosition = useCallback(() => {
    const position = returnStack[returnStack.length - 1];
    if (position === undefined) return;
    setReturnStack((stack) => stack.slice(0, -1));
    setLinkTarget(null);
    setLinkToast(null);
    const scrollArea = scrollAreaRef.current;
    if (scrollArea === null) return;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    scrollArea.scrollTo({
      top: position.top,
      left: position.left,
      behavior: reducedMotion ? 'auto' : 'smooth',
    });
  }, [returnStack]);

  useEffect(() => () => {
    if (toastTimer.current !== null) window.clearTimeout(toastTimer.current);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey || event.key !== 'ArrowLeft' || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.isComposing) return;
      const target = event.target;
      if (
        target instanceof HTMLElement
        && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
      ) {
        return;
      }
      if (returnStack.length === 0) return;
      event.preventDefault();
      popReturnPosition();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [popReturnPosition, returnStack.length]);

  const closeSections = useCallback(() => {
    setSectionsOpen(false);
  }, []);

  const clearTemporarySelection = useCallback(() => {
    const selectedPage = selection === null
      ? null
      : readerRef.current?.querySelector<HTMLElement>(`[data-pdf-page="${selection.draft.page_number}"]`);
    window.getSelection()?.removeAllRanges();
    onSelectionClear?.();
    selectedPage?.focus();
  }, [onSelectionClear, selection]);

  const captureSelection = useCallback(() => {
    const reader = readerRef.current;
    const browserSelection = window.getSelection();
    if (reader === null) return;
    if (browserSelection === null || browserSelection.rangeCount === 0) {
      onSelectionClear?.();
      return;
    }
    const result = selectionToAnchorDraft(browserSelection, reader);
    if (result.ok) onSelectionSet?.(result.draft, result.toolbarRect);
    else onSelectionClear?.();
  }, [onSelectionClear, onSelectionSet]);

  const shiftZoom = useCallback((direction: 1 | -1) => {
    setZoom((current) => nextZoom(current, direction));
  }, []);

  const resetZoom = useCallback(() => setZoom(1), []);

  useEffect(() => {
    const reader = readerRef.current;
    if (reader === null) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((current) => nextZoom(current, event.deltaY < 0 ? 1 : -1));
    };
    reader.addEventListener('wheel', onWheel, { passive: false });
    return () => reader.removeEventListener('wheel', onWheel);
  }, []);

  useEffect(() => {
    const onSelectionChange = () => captureSelection();
    document.addEventListener('selectionchange', onSelectionChange);
    return () => document.removeEventListener('selectionchange', onSelectionChange);
  }, [captureSelection]);

  useEffect(() => {
    setVisiblePages(new Set(pageNumbers.slice(0, 1)));
  }, [pageSignature, paperId]);

  useEffect(() => {
    if (pages.length === 0) {
      setPdfDocument(null);
      setStatus('ready');
      setErrorMessage(null);
      return undefined;
    }

    let mounted = true;
    const loadingTask = getDocument({ url: paperApi.getSourceUrl(paperId) });
    setPdfDocument(null);
    setStatus('loading');
    setErrorMessage(null);

    void loadingTask.promise.then(
      (pdfDocument) => {
        if (!mounted) return;
        setPdfDocument(pdfDocument);
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

  const forcedPages = new Set<number>([
    ...(activeSource !== null ? [activeSource.pageNumber] : []),
    ...(linkTarget !== null ? [linkTarget.pageNumber] : []),
  ]);
  const activePages = addOverscan(pageNumbers, new Set([...visiblePages, ...forcedPages]));

  return (
    <section
      ref={readerRef}
      className="pdf-reader"
      aria-label="论文阅读器"
      tabIndex={-1}
      onPointerUp={captureSelection}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && selection !== null) {
          event.preventDefault();
          clearTemporarySelection();
          return;
        }
        if (event.key === 'Escape' && linkTarget !== null) {
          event.preventDefault();
          setLinkTarget(null);
          return;
        }
        if (event.ctrlKey || event.metaKey) {
          if (event.key === '=' || event.key === '+') {
            event.preventDefault();
            shiftZoom(1);
          } else if (event.key === '-') {
            event.preventDefault();
            shiftZoom(-1);
          } else if (event.key === '0') {
            event.preventDefault();
            resetZoom();
          }
        }
      }}
    >
      <header className="pdf-reader__header">
        <div>
          <p className="pdf-reader__eyebrow">原始论文</p>
          <h2>PDF 阅读</h2>
        </div>
        <div className="pdf-reader__toolbar">
          {orderedPages.length > 0 ? (
            <div className="pdf-reader__zoom" role="group" aria-label="缩放控制">
              <button
                type="button"
                onClick={() => shiftZoom(-1)}
                disabled={zoom <= MIN_ZOOM}
                aria-label="缩小"
              >
                −
              </button>
              <span className="pdf-reader__zoom-level" aria-live="polite">{formatZoom(zoom)}</span>
              <button
                type="button"
                onClick={() => shiftZoom(1)}
                disabled={zoom >= MAX_ZOOM}
                aria-label="放大"
              >
                ＋
              </button>
              <button
                type="button"
                className="pdf-reader__zoom-reset"
                onClick={resetZoom}
                disabled={zoom === 1}
              >
                适配宽度
              </button>
            </div>
          ) : null}
          <a
            className="pdf-reader__source-link"
            href={paperApi.getSourceUrl(paperId)}
            target="_blank"
            rel="noreferrer"
          >
            打开原始 PDF
          </a>
        </div>
      </header>

      {activeSource !== null ? (
        <div className="pdf-reader__controls">
          <span>已定位到第 {activeSource.pageNumber} 页证据</span>
          <button type="button" onClick={onSourceCleared}>清除证据定位</button>
        </div>
      ) : null}

      {linkTarget !== null ? (
        <div className="pdf-reader__controls">
          <span>已跳转到第 {linkTarget.pageNumber} 页链接位置</span>
          <span className="pdf-reader__link-actions">
            {returnStack.length > 0 ? (
              <button type="button" onClick={popReturnPosition}>返回原位（Alt+←）</button>
            ) : null}
            <button type="button" onClick={() => setLinkTarget(null)}>清除链接定位</button>
          </span>
        </div>
      ) : null}

      {orderedPages.length === 0 ? (
        <p className="pdf-reader__empty">这篇论文没有可用的页面。</p>
      ) : (
        <div className="pdf-reader__scroll-area" ref={scrollAreaRef}>
          {sections.length > 0 ? (
            <div className="pdf-reader__section-rail">
              {sectionsOpen ? (
                <nav id="pdf-section-nav" className="pdf-reader__section-card" aria-label={sectionsTitle}>
                  <div className="pdf-reader__section-card-head">
                    <h3>{sectionsTitle}</h3>
                    <button type="button" aria-label="收起导航" onClick={closeSections}>收起</button>
                  </div>
                  <ol className="pdf-reader__section-list">
                    {sections.map((entry) => (
                      <li key={entry.id}>
                        <button
                          type="button"
                          className="pdf-reader__section-link"
                          disabled={entry.pageNumber === null}
                          title={entry.pageNumber === null ? '该章节没有可定位的页面' : undefined}
                          style={{ '--section-level': entry.level ?? 1 } as React.CSSProperties}
                          onClick={() => {
                            if (entry.pageNumber !== null) scrollToPage(entry.pageNumber);
                          }}
                        >
                          <span className="pdf-reader__section-title">{entry.title}</span>
                          {entry.pageNumber !== null && entry.title !== `第 ${entry.pageNumber} 页` ? (
                            <span className="pdf-reader__section-page">第 {entry.pageNumber} 页</span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ol>
                </nav>
              ) : (
                <button
                  ref={sectionsToggleRef}
                  type="button"
                  className="pdf-reader__section-handle"
                  aria-label={`展开${sectionsTitle}`}
                  aria-expanded={false}
                  aria-controls="pdf-section-nav"
                  onClick={() => setSectionsOpen(true)}
                >
                  ❯
                </button>
              )}
            </div>
          ) : null}
          <div className="pdf-reader__pages" aria-busy={status === 'loading'}>
          {status === 'loading' ? <p className="pdf-reader__status" role="status">正在加载原始 PDF…</p> : null}
          {status === 'error' ? <p className="pdf-reader__error" role="alert">{errorMessage}</p> : null}
          {orderedPages.map((page) => {
            const isActive = activePages.has(page.number);
            const overlays: SourceTarget[] = [];
            if (activeSource?.pageNumber === page.number) overlays.push(activeSource);
            if (linkTarget?.pageNumber === page.number && linkTarget.bbox !== null) {
              overlays.push({ id: linkTarget.id, kind: 'link', pageNumber: linkTarget.pageNumber, bbox: linkTarget.bbox });
            }
            const pageHighlights = highlights.filter(({ anchor }) => anchor.page_number === page.number);
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
                style={{
                  aspectRatio: `${page.width} / ${page.height}`,
                  // Reserve at most the rendered page width; a full-width shell
                  // would blow the aspect-ratio height up on wide panes.
                  minWidth: `min(100%, calc(${page.width}px + 2rem))`,
                }}
              >
                {pdfDocument !== null && isActive ? (
                  <PdfPageView
                    document={pdfDocument}
                    page={page}
                    active
                    overlays={overlays}
                    highlights={pageHighlights}
                    zoom={zoom}
                    onLinkNavigate={handleLinkNavigate}
                    onHighlightNote={onCreateSelectionNote === undefined ? undefined : (highlight, rect) => {
                      setManualNoteTarget({ rect, draft: {
                        quote: highlight.anchor.quote,
                        page_number: highlight.anchor.page_number,
                        rects: highlight.anchor.rects,
                        ...(highlight.anchor.element_id === null ? {} : { element_id: highlight.anchor.element_id }),
                      } });
                    }}
                    onHighlightDeleted={(highlightId) => onDeleteHighlight?.(highlightId)}
                    onHighlightColorChange={onChangeHighlightColor}
                  />
                ) : null}
              </div>
            );
          })}
          </div>
        </div>
      )}
      {linkToast !== null ? (
        <div className="pdf-reader__link-toast" role="status">
          <span>已跳转到第 {linkToast.pageNumber} 页链接位置，按 <kbd>Alt</kbd>+<kbd>←</kbd> 返回原位</span>
          <button type="button" onClick={popReturnPosition}>返回原位</button>
        </div>
      ) : null}
      {selection !== null ? (
        <SelectionToolbar
          draft={selection.draft}
          toolbarRect={selection.toolbarRect}
          onDismiss={clearTemporarySelection}
          actions={{
            explain: runSelectionAssist === undefined
              ? undefined : () => setAssistTarget({ action: 'explain', draft: selection.draft, rect: selection.toolbarRect, model: selectedModelProfileId, id: newRequestId() }),
            translate: runSelectionAssist === undefined
              ? undefined : () => setAssistTarget({ action: 'translate', draft: selection.draft, rect: selection.toolbarRect, model: selectedModelProfileId, id: newRequestId() }),
            note: onCreateSelectionNote === undefined
              ? undefined : () => setManualNoteTarget({ draft: selection.draft, rect: selection.toolbarRect }),
            ask: selectionActions?.ask === undefined
              ? undefined
              : () => selectionActions.ask?.(selection.draft),
            highlight: onCreateHighlight,
          }}
        />
      ) : null}
      {assistTarget !== null && runSelectionAssist !== undefined ? <InlineAssistantPopover
        key={assistTarget.id}
        action={assistTarget.action} draft={assistTarget.draft} toolbarRect={assistTarget.rect}
        modelProfileId={assistTarget.model} runSelectionAssist={runSelectionAssist}
        onDismiss={() => setAssistTarget(null)}
      /> : null}
      {manualNoteTarget !== null && onCreateSelectionNote !== undefined ? <ManualNotePopover draft={manualNoteTarget.draft} toolbarRect={manualNoteTarget.rect} onSave={onCreateSelectionNote} onDismiss={() => setManualNoteTarget(null)} /> : null}
    </section>
  );
}
