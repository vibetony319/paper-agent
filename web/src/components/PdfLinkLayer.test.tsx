import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PDFDocumentProxy, PDFPageProxy, PageViewport } from 'pdfjs-dist';

import { PdfLinkLayer } from './PdfLinkLayer';

const viewport = {
  convertToViewportPoint: (x: number, y: number): [number, number] => [x, y],
} as unknown as PageViewport;

function sampleAnnotations(): unknown[] {
  return [
    { subtype: 'Link', rect: [10, 20, 110, 40], dest: 'section-3' },
    { subtype: 'Link', rect: [10, 60, 110, 80], url: 'https://arxiv.org/abs/2401.00001' },
    { subtype: 'Link', rect: [10, 100, 110, 120], url: 'javascript:alert(1)' },
    { subtype: 'Widget', rect: [10, 140, 110, 160] },
  ];
}

function makeDocument() {
  return {
    // Pins the module-level preview cache key so tests share one namespace.
    docId: 'link-layer-test',
    getDestination: vi.fn(() => Promise.resolve([{ num: 9 }, { type: 'XYZ' }, 72, 648, 0])),
    getPageIndex: vi.fn(() => Promise.resolve(2)),
    getPage: vi.fn(() => Promise.resolve({
      view: [0, 0, 612, 792],
      getViewport: ({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale, convertToViewportPoint: (x: number, y: number) => [x * scale, (792 - y) * scale] }),
      render: vi.fn(() => ({ promise: Promise.resolve() })),
    })),
  };
}

function renderLayer({ annotations = sampleAnnotations(), onNavigate = vi.fn() } = {}) {
  const pdfDocument = makeDocument();
  const pdfPage = {
    getAnnotations: vi.fn(() => Promise.resolve(annotations)),
  };
  render(
    <PdfLinkLayer
      document={pdfDocument as unknown as PDFDocumentProxy}
      pdfPage={pdfPage as unknown as PDFPageProxy}
      viewport={viewport}
      pageNumber={1}
      onNavigate={onNavigate}
    />,
  );
  return { onNavigate, pdfDocument };
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('renders safe external anchors and internal navigation buttons from annotations', async () => {
  renderLayer();

  const internal = await screen.findByTestId('pdf-link-internal');
  expect(internal).toHaveAccessibleName('跳转到第 3 页');
  expect(internal).toHaveStyle({ left: '10px', top: '20px', width: '100px', height: '20px' });

  const external = screen.getByTestId('pdf-link-external');
  expect(external).toHaveAttribute('href', 'https://arxiv.org/abs/2401.00001');
  expect(external).toHaveAttribute('target', '_blank');
  expect(external).toHaveAttribute('rel', 'noopener noreferrer');

  // The javascript: URL is dropped and non-link annotations never become hitboxes.
  expect(screen.getAllByTestId('pdf-link-external')).toHaveLength(1);
});

it('resolves the destination and reports it when an internal link is clicked', async () => {
  const { onNavigate, pdfDocument } = renderLayer();

  fireEvent.click(await screen.findByTestId('pdf-link-internal'));

  expect(pdfDocument.getDestination).toHaveBeenCalledWith('section-3');
  expect(onNavigate).toHaveBeenCalledWith({
    pageNumber: 3,
    pointDestination: true,
    bbox: {
      x0: expect.closeTo(0.1051, 3),
      y0: expect.closeTo(0.1718, 3),
      x1: expect.closeTo(0.1301, 3),
      y1: expect.closeTo(0.1918, 3),
    },
  });
});

it('uses the exact PDF destination rectangle for FitR links', async () => {
  const { onNavigate } = renderLayer({ annotations: [
    { subtype: 'Link', rect: [10, 20, 80, 40], dest: [2, { name: 'FitR' }, 100, 200, 300, 400] },
  ] });
  fireEvent.click(await screen.findByTestId('pdf-link-internal'));
  expect(onNavigate).toHaveBeenCalledWith({
    pageNumber: 3,
    pointDestination: false,
    bbox: {
      x0: expect.closeTo(100 / 612, 5),
      y0: expect.closeTo(392 / 792, 5),
      x1: expect.closeTo(300 / 612, 5),
      y1: expect.closeTo(592 / 792, 5),
    },
  });
});

it('shows a destination preview after hovering a link and hides it on leave', async () => {
  renderLayer();
  const internal = await screen.findByTestId('pdf-link-internal');

  vi.useFakeTimers();
  fireEvent.mouseEnter(internal);
  await act(async () => { await vi.advanceTimersByTimeAsync(210); });

  expect(screen.getByTestId('pdf-link-preview')).toHaveTextContent('跳转到第 3 页');

  fireEvent.mouseLeave(internal);
  expect(screen.queryByTestId('pdf-link-preview')).not.toBeInTheDocument();
});

it('cancels the preview when the pointer leaves before the hover delay elapses', async () => {
  renderLayer();
  const internal = await screen.findByTestId('pdf-link-internal');

  vi.useFakeTimers();
  fireEvent.mouseEnter(internal);
  act(() => { vi.advanceTimersByTime(150); });
  fireEvent.mouseLeave(internal);
  act(() => { vi.advanceTimersByTime(150); });

  expect(screen.queryByTestId('pdf-link-preview')).not.toBeInTheDocument();
});

it('previews the url of an external link', async () => {
  renderLayer();
  const external = await screen.findByTestId('pdf-link-external');

  vi.useFakeTimers();
  fireEvent.mouseEnter(external);
  await act(async () => { await vi.advanceTimersByTimeAsync(210); });

  expect(screen.getByTestId('pdf-link-preview'))
    .toHaveTextContent('https://arxiv.org/abs/2401.00001');
});
