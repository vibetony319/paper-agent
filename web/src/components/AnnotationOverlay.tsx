import { useState } from 'react';

import type { Highlight } from '../api/types';
import { normalizedBoxStyle } from './pdfGeometry';

type AnnotationOverlayProps = {
  highlights: Highlight[];
  onAddNote?: (highlight: Highlight) => void;
  onDeleteHighlight: (highlightId: string) => void;
};

export function AnnotationOverlay({ highlights, onAddNote, onDeleteHighlight }: AnnotationOverlayProps) {
  const [menuHighlightId, setMenuHighlightId] = useState<string | null>(null);

  return (
    <div className="annotation-overlay" aria-label="论文高亮">
      {highlights.map((highlight) => (
        <div key={highlight.id} className="annotation-overlay__highlight-group">
          {highlight.anchor.rects.map((rect) => (
            <button
              key={rect.order}
              type="button"
              className="annotation-overlay__highlight"
              style={normalizedBoxStyle(rect)}
              aria-label={`高亮：${highlight.anchor.quote}`}
              onClick={() => setMenuHighlightId((current) => current === highlight.id ? null : highlight.id)}
            />
          ))}
          {menuHighlightId === highlight.id ? (
            <div className="annotation-overlay__menu" role="menu">
              <button
                type="button"
                role="menuitem"
                disabled={onAddNote === undefined}
                title={onAddNote === undefined ? '暂不可用' : undefined}
                onClick={() => onAddNote?.(highlight)}
              >
                记笔记
              </button>
              <button type="button" role="menuitem" onClick={() => onDeleteHighlight(highlight.id)}>删除高亮</button>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
