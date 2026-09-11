import { useState } from 'react';

import type { Highlight } from '../api/types';
import { normalizedBoxStyle } from './pdfGeometry';

type AnnotationOverlayProps = {
  highlights: Highlight[];
  onAddNote?: (highlight: Highlight, rect: DOMRect) => void;
  onDeleteHighlight: (highlightId: string) => void;
};

export function AnnotationOverlay({ highlights, onAddNote, onDeleteHighlight }: AnnotationOverlayProps) {
  const [menuHighlight, setMenuHighlight] = useState<{ id: string; rect: DOMRect } | null>(null);

  return (
    <div className="annotation-overlay" role="group" aria-label="论文高亮">
      {highlights.map((highlight) => (
        <div key={highlight.id} className="annotation-overlay__highlight-group">
          {highlight.anchor.rects.map((rect) => (
            <button
              key={rect.order}
              type="button"
              className="annotation-overlay__highlight"
              style={normalizedBoxStyle(rect)}
              aria-label={`高亮：${highlight.anchor.quote}`}
              onClick={(event) => setMenuHighlight((current) => current?.id === highlight.id ? null : { id: highlight.id, rect: event.currentTarget.getBoundingClientRect() })}
            />
          ))}
          {menuHighlight?.id === highlight.id ? (
            <div className="annotation-overlay__menu" role="menu">
              <button
                type="button"
                role="menuitem"
                disabled={onAddNote === undefined}
                title={onAddNote === undefined ? '暂不可用' : undefined}
                onClick={() => onAddNote?.(highlight, menuHighlight.rect)}
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
