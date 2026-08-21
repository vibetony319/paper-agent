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
