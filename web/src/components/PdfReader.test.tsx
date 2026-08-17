import { useLayoutEffect, useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const pdf = vi.hoisted(() => {
  const renderTasks: Array<{ cancel: ReturnType<typeof vi.fn>; promise: Promise<void> }> = [];
  const render = vi.fn(() => {
    const task = { cancel: vi.fn(), promise: Promise.resolve() };
    renderTasks.push(task);
    return task;
  });
  const getPage = vi.fn(() => Promise.resolve({
    getViewport: () => ({ width: 640, height: 880 }),
    render,
  }));
  const destroy = vi.fn(() => Promise.resolve());
  const getDocument = vi.fn(() => ({
    destroy,
    promise: Promise.resolve({ getPage }),
  }));

  return { destroy, getDocument, getPage, render, renderTasks };
});

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: pdf.getDocument,
}));

import { PdfReader } from './PdfReader';

beforeEach(() => {
  vi.clearAllMocks();
  pdf.renderTasks.splice(0);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({} as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('keeps navigation within parsed pages and only shows the active evidence on its page', async () => {
  render(
    <PdfReader
      paperId="paper id"
      pages={[
        { id: 'page-1', number: 1, width: 612, height: 792 },
        { id: 'page-2', number: 2, width: 612, height: 792 },
      ]}
      activeSource={{
        id: 'element-2',
        kind: 'paragraph',
        pageNumber: 2,
        bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
      }}
      onSourceCleared={vi.fn()}
    />,
  );

  const pageNumber = screen.getByLabelText('Page number');
  expect(pageNumber).toHaveValue(2);
  expect(pageNumber).toHaveAttribute('min', '1');
  expect(pageNumber).toHaveAttribute('max', '2');

  await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(2));
  expect(screen.getByTestId('source-overlay')).toHaveStyle({
    left: '10%', top: '20%', width: '70%', height: '10%',
  });

  fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));

  await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(1));
  expect(screen.queryByTestId('source-overlay')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
  expect(screen.getByRole('link', { name: 'Open original PDF' }))
    .toHaveAttribute('href', '/api/papers/paper%20id/source');
});

it('positions normalized evidence in the rendered page coordinate space', async () => {
  render(
    <PdfReader
      paperId="paper-a"
      pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
      activeSource={{
        id: 'element-1',
        kind: 'paragraph',
        pageNumber: 1,
        bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
      }}
      onSourceCleared={vi.fn()}
    />,
  );

  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());

  const canvas = screen.getByRole('img', { name: 'Rendered PDF page 1' });
  const overlay = screen.getByTestId('source-overlay');
  expect(overlay.parentElement).toBe(canvas.parentElement);
  expect(overlay.parentElement).not.toHaveClass('pdf-reader__page');
});

it('shows a public-safe error when the canvas cannot render', async () => {
  vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValueOnce(null);

  render(
    <PdfReader
      paperId="paper-a"
      pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Unable to render the original PDF.',
  );
  expect(screen.queryByText(/canvas context/i)).not.toBeInTheDocument();
});

it('clamps page input to the nearest parsed page', async () => {
  render(
    <PdfReader
      paperId="paper-a"
      pages={[
        { id: 'page-2', number: 2, width: 612, height: 792 },
        { id: 'page-5', number: 5, width: 612, height: 792 },
        { id: 'page-9', number: 9, width: 612, height: 792 },
      ]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  const pageNumber = screen.getByLabelText('Page number');
  fireEvent.change(pageNumber, { target: { value: '7' } });
  expect(pageNumber).toHaveValue(5);

  fireEvent.change(pageNumber, { target: { value: '99' } });
  expect(pageNumber).toHaveValue(9);

  fireEvent.change(pageNumber, { target: { value: '-4' } });
  expect(pageNumber).toHaveValue(2);
  await waitFor(() => expect(pdf.getPage).toHaveBeenLastCalledWith(2));
});

it('renders an empty state without starting PDF.js when no parsed pages exist', () => {
  render(
    <PdfReader
      paperId="paper-a"
      pages={[]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  expect(screen.getByText('No parsed pages are available for this paper.')).toBeVisible();
  expect(pdf.getDocument).not.toHaveBeenCalled();
  expect(screen.getByRole('link', { name: 'Open original PDF' })).toBeVisible();
});

it('does not expose PDF.js failure details to readers', async () => {
  pdf.getDocument.mockReturnValueOnce({
    destroy: vi.fn(() => Promise.resolve()),
    promise: Promise.reject(new Error('Token abc123 rejected by internal PDF endpoint')),
  });

  render(
    <PdfReader
      paperId="paper-a"
      pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Unable to render the original PDF.',
  );
  expect(screen.queryByText(/abc123|internal PDF endpoint/i)).not.toBeInTheDocument();
});

it('clears the active evidence through the workspace callback', async () => {
  const onSourceCleared = vi.fn();
  render(
    <PdfReader
      paperId="paper-a"
      pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
      activeSource={{
        id: 'element-1',
        kind: 'paragraph',
        pageNumber: 1,
        bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
      }}
      onSourceCleared={onSourceCleared}
    />,
  );

  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Clear evidence highlight' }));
  expect(onSourceCleared).toHaveBeenCalledOnce();
});

it('cancels an obsolete render so an earlier page cannot paint over the current page', async () => {
  let resolveFirstRender: (() => void) | undefined;
  const firstRender = {
    cancel: vi.fn(),
    promise: new Promise<void>((resolve) => {
      resolveFirstRender = resolve;
    }),
  };
  const secondRender = { cancel: vi.fn(), promise: Promise.resolve() };
  pdf.render.mockReturnValueOnce(firstRender).mockReturnValueOnce(secondRender);

  render(
    <PdfReader
      paperId="paper-a"
      pages={[
        { id: 'page-1', number: 1, width: 612, height: 792 },
        { id: 'page-2', number: 2, width: 612, height: 792 },
      ]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  await waitFor(() => expect(pdf.render).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() => expect(pdf.render).toHaveBeenCalledTimes(2));

  expect(firstRender.cancel).toHaveBeenCalledOnce();
  expect(pdf.destroy).toHaveBeenCalledOnce();
  resolveFirstRender?.();

  await waitFor(() => expect(screen.getByText('Page 2 of 2')).toBeVisible());
  expect(screen.getByRole('img', { name: 'Rendered PDF page 2' }))
    .toHaveAttribute('width', '640');
});

it('does not render an obsolete page into a replacement canvas before effect cleanup', async () => {
  type PdfPage = Awaited<ReturnType<typeof pdf.getPage>>;
  type GetPage = () => Promise<PdfPage>;
  type RenderPage = () => ReturnType<typeof pdf.render>;

  let resolveStalePage: ((page: PdfPage) => void) | undefined;
  const staleRender = vi.fn<RenderPage>(() => ({
    cancel: vi.fn(),
    promise: Promise.resolve(),
  }));
  const staleGetPage = vi.fn<GetPage>(() => new Promise<PdfPage>((resolve) => {
    resolveStalePage = resolve;
  }));
  const staleTask: ReturnType<typeof pdf.getDocument> = {
    destroy: vi.fn(() => Promise.resolve()),
    promise: Promise.resolve({ getPage: staleGetPage }),
  };
  pdf.getDocument.mockReturnValueOnce(staleTask);

  function ReplacementHarness() {
    const [paperId, setPaperId] = useState('paper-a');

    useLayoutEffect(() => {
      if (paperId === 'paper-b') {
        resolveStalePage?.({
          getViewport: () => ({ width: 320, height: 440 }),
          render: staleRender,
        });
      }
    }, [paperId]);

    return (
      <>
        <button type="button" onClick={() => setPaperId('paper-b')}>Replace paper</button>
        <PdfReader
          paperId={paperId}
          pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
          activeSource={null}
          onSourceCleared={vi.fn()}
        />
      </>
    );
  }

  render(<ReplacementHarness />);
  await waitFor(() => expect(staleGetPage).toHaveBeenCalledWith(1));
  const originalCanvas = screen.getByRole('img', { name: 'Rendered PDF page 1' });

  screen.getByRole('button', { name: 'Replace paper' }).click();
  await Promise.resolve();
  const replacementCanvas = screen.getByRole('img', { name: 'Rendered PDF page 1' });
  expect(replacementCanvas).not.toBe(originalCanvas);

  await waitFor(() => expect(pdf.render).toHaveBeenCalledOnce());
  expect(staleRender).not.toHaveBeenCalled();
});

it('destroys a superseded loading task before its stale document can render', async () => {
  const staleGetPage = vi.fn<() => ReturnType<typeof pdf.getPage>>();
  type StaleDocument = { getPage: typeof staleGetPage };
  let resolveStaleDocument: ((document: StaleDocument) => void) | undefined;
  const staleDestroy = vi.fn(() => Promise.resolve());
  const staleTask = {
    destroy: staleDestroy,
    promise: new Promise<StaleDocument>((resolve) => {
      resolveStaleDocument = resolve;
    }),
  };
  pdf.getDocument.mockReturnValueOnce(staleTask);

  const { rerender } = render(
    <PdfReader
      paperId="paper-a"
      pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  rerender(
    <PdfReader
      paperId="paper-b"
      pages={[{ id: 'page-1', number: 1, width: 612, height: 792 }]}
      activeSource={null}
      onSourceCleared={vi.fn()}
    />,
  );

  expect(staleDestroy).toHaveBeenCalledOnce();
  resolveStaleDocument?.({ getPage: staleGetPage });

  await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(1));
  expect(staleGetPage).not.toHaveBeenCalled();
  expect(screen.getByRole('img', { name: 'Rendered PDF page 1' })).toBeVisible();
});
