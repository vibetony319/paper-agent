import { expect, it } from 'vitest';

import { selectionToAnchorDraft } from './pdfSelection';

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return {
    bottom: top + height,
    height,
    left,
    right: left + width,
    toJSON: () => ({}),
    top,
    width,
    x: left,
    y: top,
  } as DOMRect;
}

function selectionFixture(
  text: string,
  rects: DOMRect[],
  anchorNode: Node,
  focusNode = anchorNode,
): Selection {
  const range = {
    getClientRects: () => rects,
  } as unknown as Range;
  return {
    anchorNode,
    focusNode,
    rangeCount: 1,
    getRangeAt: () => range,
    toString: () => text,
  } as unknown as Selection;
}

function readerWithPage(pageNumber = 2) {
  const reader = document.createElement('div');
  const page = document.createElement('div');
  page.dataset.pdfPage = String(pageNumber);
  const surface = document.createElement('div');
  surface.className = 'pdf-page-view__surface';
  const textLayer = document.createElement('div');
  textLayer.className = 'pdf-page-view__text-layer';
  const text = document.createTextNode('selected text');
  textLayer.append(text);
  surface.append(textLayer);
  page.append(surface);
  reader.append(page);
  document.body.append(reader);
  Object.defineProperty(surface, 'getBoundingClientRect', {
    value: () => rect(100, 200, 600, 800),
  });
  return { page, reader, surface, text };
}

it('normalizes multiple client rects inside one pdf page', () => {
  const { reader, text } = readerWithPage();
  const selection = selectionFixture('selected text', [
    rect(160, 280, 300, 20),
    rect(160, 305, 180, 20),
  ], text);

  expect(selectionToAnchorDraft(selection, reader)).toMatchObject({
    ok: true,
    draft: {
      quote: 'selected text',
      page_number: 2,
      rects: [
        { order: 0, x0: 0.1, y0: 0.1, x1: 0.6, y1: 0.125 },
        { order: 1, x0: 0.1, y0: 0.13125, x1: 0.4, y1: 0.15625 },
      ],
    },
  });
});

it('rejects cross-page, empty, and non-text-layer selections', () => {
  const { reader, text } = readerWithPage();
  const other = readerWithPage(3).text;
  const outside = document.createTextNode('outside');

  expect(selectionToAnchorDraft(selectionFixture('text', [rect(160, 280, 20, 20)], text, other), reader))
    .toEqual({ ok: false, reason: 'cross_page' });
  expect(selectionToAnchorDraft(selectionFixture(' 。！ ', [rect(160, 280, 20, 20)], text), reader))
    .toEqual({ ok: false, reason: 'empty' });
  expect(selectionToAnchorDraft(selectionFixture('outside', [rect(160, 280, 20, 20)], outside), reader))
    .toEqual({ ok: false, reason: 'outside_reader' });
});

it('clips rectangles to the page surface and discards zero-area rectangles', () => {
  const { reader, text } = readerWithPage();
  const selection = selectionFixture('selected text', [
    rect(50, 150, 100, 100),
    rect(200, 300, 0, 20),
  ], text);

  expect(selectionToAnchorDraft(selection, reader)).toMatchObject({
    ok: true,
    draft: {
      rects: [{ order: 0, x0: 0, y0: 0, x1: 1 / 12, y1: 1 / 16 }],
    },
  });
});
