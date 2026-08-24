import type { BoundingBox, DocumentElement, TextAnchor } from '../api/types';
import type { SourceTarget } from './types';

export type LocatedDocumentElement = DocumentElement & {
  page_number: number;
  bbox: BoundingBox;
};

export function hasValidSourceLocation(
  element: DocumentElement,
): element is LocatedDocumentElement {
  if (
    element.location_status !== 'located'
    || !Number.isInteger(element.page_number)
    || element.page_number === null
    || element.page_number <= 0
    || element.bbox === null
  ) {
    return false;
  }

  const { x0, y0, x1, y1 } = element.bbox;
  const coordinates = [x0, y0, x1, y1];
  return coordinates.every((coordinate) => (
    Number.isFinite(coordinate) && coordinate >= 0 && coordinate <= 1
  )) && x0 < x1 && y0 < y1;
}

export function toAnchorSourceTarget(anchor: TextAnchor): SourceTarget | null {
  if (!Number.isInteger(anchor.page_number) || anchor.page_number <= 0 || anchor.rects.length === 0) {
    return null;
  }
  const bbox = anchor.rects.reduce<BoundingBox | null>((current, rect) => {
    const valid = [rect.x0, rect.y0, rect.x1, rect.y1]
      .every((value) => Number.isFinite(value) && value >= 0 && value <= 1)
      && rect.x0 < rect.x1 && rect.y0 < rect.y1;
    if (!valid) return current;
    return current === null ? { x0: rect.x0, y0: rect.y0, x1: rect.x1, y1: rect.y1 } : {
      x0: Math.min(current.x0, rect.x0), y0: Math.min(current.y0, rect.y0),
      x1: Math.max(current.x1, rect.x1), y1: Math.max(current.y1, rect.y1),
    };
  }, null);
  return bbox === null ? null : {
    id: anchor.id, kind: 'text_anchor', pageNumber: anchor.page_number, bbox,
  };
}
