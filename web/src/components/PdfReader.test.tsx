import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const pdf = vi.hoisted(() => {
  const viewport = { width: 612, height: 792 };
  const render = vi.fn(() => ({ cancel: vi.fn(), promise: Promise.resolve() }));
  const getPage = vi.fn(() => Promise.resolve({
    getViewport: vi.fn(() => viewport),
    render,
    streamTextContent: vi.fn(() => ({ getReader: vi.fn() })),
  }));
  const destroy = vi.fn(() => Promise.resolve());
  const getDocument = vi.fn(() => ({ destroy, promise: Promise.resolve({ getPage }) }));
  const TextLayer = vi.fn(function TextLayer() {
    return { render: vi.fn(() => Promise.resolve()), cancel: vi.fn() };
  });

  return { TextLayer, destroy, getDocument, getPage, render };
});

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: pdf.getDocument,
  TextLayer: pdf.TextLayer,
}));

import { PdfReader } from './PdfReader';

const manyPages = Array.from({ length: 20 }, (_, index) => ({
  id: `page-${index + 1}`,
  number: index + 1,
  width: 612,
  height: 792,
}));

class IntersectionObserverStub {
  static instances: IntersectionObserverStub[] = [];
  readonly observe = vi.fn();
  readonly unobserve = vi.fn();
  readonly disconnect = vi.fn();

  constructor(
    readonly callback: IntersectionObserverCallback,
    readonly options?: IntersectionObserverInit,
  ) {
    IntersectionObserverStub.instances.push(this);
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  IntersectionObserverStub.instances.splice(0);
  vi.stubGlobal('IntersectionObserver', IntersectionObserverStub);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({} as CanvasRenderingContext2D);
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('keeps an aspect ratio page shell until a page enters the overscan area', async () => {
  render(
    <PdfReader paperId="paper-a" pages={manyPages} activeSource={null} onSourceCleared={vi.fn()} />,
  );

  expect(screen.getByTestId('pdf-page-shell-20')).toHaveStyle({ aspectRatio: '612 / 792' });
  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledOnce());
  expect(pdf.getPage).not.toHaveBeenCalledWith(20);
});

it('creates one document loading task for all active pages of a paper', async () => {
  render(
    <PdfReader paperId="paper-a" pages={manyPages} activeSource={null} onSourceCleared={vi.fn()} />,
  );

  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledOnce());
  const observer = IntersectionObserverStub.instances[0];
  const pageTwo = screen.getByTestId('pdf-page-shell-2');
  observer.callback([{ isIntersecting: true, target: pageTwo } as unknown as IntersectionObserverEntry], observer as never);

  await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(2));
  expect(pdf.getDocument).toHaveBeenCalledOnce();
  expect(observer.options).toMatchObject({ rootMargin: '1200px 0px' });
});

it('activates and scrolls an evidence target while retaining its overlay', async () => {
  render(
    <PdfReader
      paperId="paper-a"
      pages={manyPages}
      activeSource={{
        id: 'element-5',
        kind: 'paragraph',
        pageNumber: 5,
        bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
      }}
      onSourceCleared={vi.fn()}
    />,
  );

  await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(5));
  expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith(
    expect.objectContaining({ block: 'center' }),
  );
  expect(screen.getByTestId('source-overlay-5')).toHaveStyle({
    left: '10%', top: '20%', width: '70%', height: '10%',
  });
});

it('destroys a loading task when the selected paper changes', async () => {
  const staleDestroy = vi.fn(() => Promise.resolve());
  let resolveStaleDocument: ((document: { getPage: typeof pdf.getPage }) => void) | undefined;
  pdf.getDocument.mockReturnValueOnce({
    destroy: staleDestroy,
    promise: new Promise((resolve) => { resolveStaleDocument = resolve; }),
  });

  const { rerender } = render(
    <PdfReader paperId="paper-a" pages={manyPages} activeSource={null} onSourceCleared={vi.fn()} />,
  );
  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledOnce());

  rerender(
    <PdfReader paperId="paper-b" pages={manyPages} activeSource={null} onSourceCleared={vi.fn()} />,
  );

  expect(staleDestroy).toHaveBeenCalledOnce();
  resolveStaleDocument?.({ getPage: pdf.getPage });
  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledTimes(2));
});

it('does not load PDF.js when the paper has no parsed pages', () => {
  render(<PdfReader paperId="paper-a" pages={[]} activeSource={null} onSourceCleared={vi.fn()} />);

  expect(screen.getByText('这篇论文没有可用的页面。')).toBeVisible();
  expect(pdf.getDocument).not.toHaveBeenCalled();
  expect(screen.getByRole('link', { name: '打开原始 PDF' }))
    .toHaveAttribute('href', '/api/papers/paper-a/source');
});

it('clears an active evidence target through the workspace callback', () => {
  const onSourceCleared = vi.fn();
  render(
    <PdfReader
      paperId="paper-a"
      pages={manyPages}
      activeSource={{
        id: 'element-2',
        kind: 'paragraph',
        pageNumber: 2,
        bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
      }}
      onSourceCleared={onSourceCleared}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '清除证据定位' }));
  expect(onSourceCleared).toHaveBeenCalledOnce();
});

it('converts a pointer selection from the PDF text layer into an anchor draft', async () => {
  const onSelectionSet = vi.fn();
  render(
    <PdfReader
      paperId="paper-a"
      pages={[manyPages[0]]}
      activeSource={null}
      onSourceCleared={vi.fn()}
      onSelectionSet={onSelectionSet}
    />,
  );

  const textLayer = await screen.findByTestId('pdf-text-layer-1');
  const text = document.createTextNode('selected text');
  textLayer.append(text);
  const surface = textLayer.closest('.pdf-page-view__surface') as HTMLElement;
  Object.defineProperty(surface, 'getBoundingClientRect', {
    configurable: true,
    value: () => ({ left: 100, top: 200, right: 700, bottom: 1000, width: 600, height: 800 }),
  });
  const selection = {
    anchorNode: text,
    focusNode: text,
    rangeCount: 1,
    getRangeAt: () => ({
      getClientRects: () => [{ left: 160, top: 280, right: 460, bottom: 300, width: 300, height: 20 }],
    }),
    toString: () => 'selected text',
  } as unknown as Selection;
  vi.spyOn(window, 'getSelection').mockReturnValue(selection);

  fireEvent.pointerUp(textLayer);

  expect(onSelectionSet).toHaveBeenCalledWith({
    quote: 'selected text', page_number: 1,
    rects: [{ order: 0, x0: 0.1, y0: 0.1, x1: 0.6, y1: 0.125 }],
  }, expect.objectContaining({ left: 160, top: 280 }));
});
