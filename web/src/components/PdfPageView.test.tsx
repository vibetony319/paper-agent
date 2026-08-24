import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const pdf = vi.hoisted(() => {
  const viewport = { width: 612, height: 792 };
  const render = vi.fn(() => ({ cancel: vi.fn(), promise: Promise.resolve() }));
  const getViewport = vi.fn<(args: { scale: number }) => { width: number; height: number }>(() => viewport);
  const streamTextContent = vi.fn(() => ({ getReader: vi.fn() }));
  const getPage = vi.fn(() => Promise.resolve({
    getViewport,
    render,
    streamTextContent,
  }));
  const textLayerRender = vi.fn(() => Promise.resolve());
  const textLayerCancel = vi.fn();
  const TextLayer = vi.fn(function TextLayer() {
    return { render: textLayerRender, cancel: textLayerCancel };
  });

  return {
    TextLayer,
    getPage,
    getViewport,
    render,
    textLayerCancel,
    textLayerRender,
    viewport,
  };
});

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: vi.fn(),
  TextLayer: pdf.TextLayer,
}));

import { PdfPageView } from './PdfPageView';

const document = { getPage: pdf.getPage };
const page = { id: 'page-1', number: 1, width: 612, height: 792 };

class ResizeObserverStub {
  static instances: ResizeObserverStub[] = [];
  readonly disconnect = vi.fn();
  readonly observe = vi.fn();

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverStub.instances.push(this);
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  pdf.getViewport.mockImplementation(() => pdf.viewport);
  ResizeObserverStub.instances.splice(0);
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({} as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('renders canvas and text layer from the same viewport', async () => {
  render(<PdfPageView document={document as never} page={page} active overlays={[]} />);

  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());
  expect(pdf.TextLayer).toHaveBeenCalledWith(expect.objectContaining({
    container: screen.getByTestId('pdf-text-layer-1'),
    viewport: pdf.viewport,
  }));
  expect(pdf.textLayerRender).toHaveBeenCalledOnce();
});

it('uses a high-DPI backing store while retaining the viewport-sized surface', async () => {
  vi.spyOn(window, 'devicePixelRatio', 'get').mockReturnValue(2);
  render(<PdfPageView document={document as never} page={page} active overlays={[]} />);

  const canvas = await screen.findByRole('img', { name: 'PDF 第 1 页' });
  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());
  expect(canvas).toHaveAttribute('width', '1224');
  expect(canvas).toHaveAttribute('height', '1584');
  expect(canvas).toHaveStyle({ width: '612px', height: '792px' });
});

it('cancels the text layer and canvas render when the page deactivates', async () => {
  const { rerender } = render(
    <PdfPageView document={document as never} page={page} active overlays={[]} />,
  );
  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());

  rerender(<PdfPageView document={document as never} page={page} active={false} overlays={[]} />);

  expect(pdf.textLayerCancel).toHaveBeenCalledOnce();
  expect(pdf.render.mock.results[0]?.value.cancel).toHaveBeenCalledOnce();
});

it('recreates the shared viewport after its container narrows', async () => {
  render(<PdfPageView document={document as never} page={page} active overlays={[]} />);
  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());
  const observer = await waitFor(() => {
    expect(ResizeObserverStub.instances).toHaveLength(1);
    return ResizeObserverStub.instances[0];
  });

  observer.callback([{ contentRect: { width: 306 } } as ResizeObserverEntry], observer as never);

  await waitFor(() => expect(pdf.render).toHaveBeenCalledTimes(2));
  expect(pdf.getViewport).toHaveBeenLastCalledWith({ scale: 0.5 });
});

it('uses the page shell width instead of a self-sized surface for its viewport', async () => {
  const narrowViewport = { width: 306, height: 396 };
  pdf.getViewport.mockImplementation(({ scale }) => scale === 0.5 ? narrowViewport : pdf.viewport);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function clientWidth(this: HTMLElement) {
    if (this.classList.contains('pdf-reader__page-shell')) return 306;
    if (this.classList.contains('pdf-page-view__surface')) return 612;
    return 0;
  });

  render(
    <div className="pdf-reader__page-shell">
      <PdfPageView document={document as never} page={page} active overlays={[]} />
    </div>,
  );

  const canvas = await screen.findByRole('img', { name: 'PDF 第 1 页' });
  await waitFor(() => expect(canvas).toHaveStyle({ width: '306px', height: '396px' }));
  expect(pdf.TextLayer).toHaveBeenCalledWith(expect.objectContaining({ viewport: narrowViewport }));
});

it('keeps the canvas visible when the page has no selectable text', async () => {
  render(<PdfPageView document={document as never} page={page} active overlays={[]} />);

  expect(await screen.findByText('该页无法选择文字')).toBeVisible();
  expect(screen.getByRole('img', { name: 'PDF 第 1 页' })).toBeVisible();
});

it('shows a public-safe error when the canvas is unavailable', async () => {
  vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValueOnce(null);
  render(<PdfPageView document={document as never} page={page} active overlays={[]} />);

  expect(await screen.findByRole('alert')).toHaveTextContent('该页暂时无法显示。');
  expect(screen.queryByText(/Canvas rendering is unavailable/i)).not.toBeInTheDocument();
});
