import type { BoundingBox, DocumentElement } from '../api/types';

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
