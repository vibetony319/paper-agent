import type { TextAnchorDraft } from '../api/types';

export type SelectionResult =
  | { ok: true; draft: TextAnchorDraft; toolbarRect: DOMRect }
  | { ok: false; reason: 'empty' | 'cross_page' | 'outside_reader' };

function closestElement(node: Node | null, selector: string): HTMLElement | null {
  if (node === null) return null;
  const element = node.nodeType === Node.ELEMENT_NODE
    ? node as HTMLElement
    : node.parentElement;
  return element?.closest<HTMLElement>(selector) ?? null;
}

function isMeaningful(text: string): boolean {
  return text.replace(/[\s\p{P}\p{S}]/gu, '').length > 0;
}

function rectangle(left: number, top: number, right: number, bottom: number): DOMRect {
  return {
    bottom,
    height: bottom - top,
    left,
    right,
    toJSON: () => ({}),
    top,
    width: right - left,
    x: left,
    y: top,
  } as DOMRect;
}

export function selectionToAnchorDraft(selection: Selection, reader: HTMLElement): SelectionResult {
  const quote = selection.toString().trim();
  if (!isMeaningful(quote)) return { ok: false, reason: 'empty' };
  if (selection.rangeCount === 0) return { ok: false, reason: 'empty' };

  const anchorPage = closestElement(selection.anchorNode, '[data-pdf-page]');
  const focusPage = closestElement(selection.focusNode, '[data-pdf-page]');
  if (anchorPage !== null && focusPage !== null && anchorPage !== focusPage) {
    return { ok: false, reason: 'cross_page' };
  }
  if (anchorPage === null || focusPage === null || !reader.contains(anchorPage) || !reader.contains(focusPage)) {
    return { ok: false, reason: 'outside_reader' };
  }
  if (
    closestElement(selection.anchorNode, '.pdf-page-view__text-layer') === null
    || closestElement(selection.focusNode, '.pdf-page-view__text-layer') === null
  ) {
    return { ok: false, reason: 'outside_reader' };
  }

  const surface = anchorPage.querySelector<HTMLElement>('.pdf-page-view__surface');
  const pageNumber = Number(anchorPage.dataset.pdfPage);
  if (surface === null || !Number.isInteger(pageNumber)) return { ok: false, reason: 'outside_reader' };

  const surfaceRect = surface.getBoundingClientRect();
  if (surfaceRect.width <= 0 || surfaceRect.height <= 0) return { ok: false, reason: 'outside_reader' };
  const rects = Array.from(selection.getRangeAt(0).getClientRects())
    .map((clientRect, order) => {
      const left = Math.max(surfaceRect.left, clientRect.left);
      const top = Math.max(surfaceRect.top, clientRect.top);
      const right = Math.min(surfaceRect.right, clientRect.right);
      const bottom = Math.min(surfaceRect.bottom, clientRect.bottom);
      if (right <= left || bottom <= top) return null;
      return {
        clientRect: rectangle(left, top, right, bottom),
        rect: {
          order,
          x0: (left - surfaceRect.left) / surfaceRect.width,
          y0: (top - surfaceRect.top) / surfaceRect.height,
          x1: (right - surfaceRect.left) / surfaceRect.width,
          y1: (bottom - surfaceRect.top) / surfaceRect.height,
        },
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null);
  if (rects.length === 0) return { ok: false, reason: 'empty' };

  return {
    ok: true,
    draft: {
      quote,
      page_number: pageNumber,
      rects: rects.map(({ rect }, order) => ({ ...rect, order })),
    },
    toolbarRect: rects[rects.length - 1].clientRect,
  };
}
