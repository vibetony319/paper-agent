import type { CSSProperties } from 'react';

import type { BoundingBox } from '../api/types';
import type { SourceTarget } from '../workspace/types';

function percentage(value: number): string {
  return `${Number((value * 100).toFixed(6))}%`;
}

export function normalizedBoxStyle(bbox: BoundingBox): CSSProperties {
  return {
    left: percentage(bbox.x0),
    top: percentage(bbox.y0),
    width: percentage(bbox.x1 - bbox.x0),
    height: percentage(bbox.y1 - bbox.y0),
  };
}

export function sourceOverlayStyle(source: SourceTarget | null): CSSProperties | null {
  return source === null ? null : normalizedBoxStyle(source.bbox);
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** Expand a PDF point destination to the text line it introduces. */
export function linkTextLineBox(
  surface: HTMLElement,
  textLayer: HTMLElement,
  point: BoundingBox,
): BoundingBox {
  const pageRect = surface.getBoundingClientRect();
  const fallback: BoundingBox = {
    x0: clamp01(point.x0 - 0.005),
    y0: clamp01(point.y0 - 0.008),
    x1: clamp01(Math.max(point.x1, point.x0 + 0.35)),
    y1: clamp01(Math.max(point.y1, point.y0 + 0.025)),
  };
  if (pageRect.width <= 0 || pageRect.height <= 0) return fallback;

  const anchorX = pageRect.left + ((point.x0 + point.x1) / 2) * pageRect.width;
  const anchorY = pageRect.top + ((point.y0 + point.y1) / 2) * pageRect.height;
  const textRects = Array.from(textLayer.querySelectorAll<HTMLElement>('span'))
    .filter((span) => span.textContent?.trim())
    .map((span) => span.getBoundingClientRect())
    .filter((rect) => rect.width > 1 && rect.height > 1
      && rect.right > pageRect.left && rect.left < pageRect.right
      && rect.bottom > pageRect.top && rect.top < pageRect.bottom);
  if (textRects.length === 0) return fallback;

  // XYZ destinations normally mark the viewport's top edge, a little above
  // the heading. The preceding paragraph can be physically closer to that
  // point than the heading, so look forward first.
  const forwardRects = textRects.filter((rect) => rect.top >= anchorY - 4);
  const candidates = forwardRects.length > 0 ? forwardRects : textRects;
  const distanceTo = (rect: DOMRect) => {
    const dx = Math.max(rect.left - anchorX, anchorX - rect.right, 0);
    const dy = Math.max(rect.top - anchorY, anchorY - rect.bottom, 0);
    return dy * 3 + dx * 0.2;
  };
  const nearest = candidates.reduce((best, rect) => distanceTo(rect) < distanceTo(best) ? rect : best);
  const row = textRects
    .filter((rect) => Math.abs((rect.top + rect.bottom - nearest.top - nearest.bottom) / 2)
      <= Math.max(4, (rect.height + nearest.height) * 0.35))
    .sort((left, right) => left.left - right.left);
  const index = row.indexOf(nearest);
  const maxGap = Math.max(18, nearest.height * 2.5);
  let start = index;
  let end = index;
  while (start > 0 && row[start].left - row[start - 1].right <= maxGap) start -= 1;
  while (end < row.length - 1 && row[end + 1].left - row[end].right <= maxGap) end += 1;
  const line = row.slice(start, end + 1);
  const left = Math.min(...line.map((rect) => rect.left)) - 3;
  const top = Math.min(...line.map((rect) => rect.top)) - 2;
  const right = Math.max(...line.map((rect) => rect.right)) + 3;
  const bottom = Math.max(...line.map((rect) => rect.bottom)) + 2;
  return {
    x0: clamp01((left - pageRect.left) / pageRect.width),
    y0: clamp01((top - pageRect.top) / pageRect.height),
    x1: clamp01((right - pageRect.left) / pageRect.width),
    y1: clamp01((bottom - pageRect.top) / pageRect.height),
  };
}
