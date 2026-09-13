import { useEffect, useRef, useState } from 'react';

import type { Highlight, HighlightColor } from '../api/types';
import { normalizedBoxStyle } from './pdfGeometry';

type AnnotationOverlayProps = {
  highlights: Highlight[];
  onAddNote?: (highlight: Highlight, rect: DOMRect) => void;
  onDeleteHighlight: (highlightId: string) => void;
  onChangeColor?: (highlightId: string, color: HighlightColor) => void;
};

const HIGHLIGHT_COLORS: Array<{ value: HighlightColor; label: string }> = [
  { value: 'yellow', label: '黄色' },
  { value: 'green', label: '绿色' },
  { value: 'blue', label: '蓝色' },
  { value: 'pink', label: '粉色' },
];

const MENU_WIDTH = 176;

type MenuState = {
  highlightId: string;
  rectOrder: number;
  rect: DOMRect;
  left: number;
  top: number;
  maxWidth: number;
};

export function AnnotationOverlay({
  highlights,
  onAddNote,
  onDeleteHighlight,
  onChangeColor,
}: AnnotationOverlayProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);

  useEffect(() => {
    if (menu === null) return undefined;
    const belongsToOverlay = (target: EventTarget | null): boolean => (
      target instanceof HTMLElement
      && target.closest('.annotation-overlay__highlight, .annotation-overlay__menu') !== null
    );
    const onPointerDown = (event: PointerEvent) => {
      if (!belongsToOverlay(event.target)) setMenu(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenu(null);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [menu]);

  const openMenu = (highlight: Highlight, rectOrder: number, button: HTMLButtonElement) => {
    const container = containerRef.current;
    if (container === null) return;
    const containerRect = container.getBoundingClientRect();
    const buttonRect = button.getBoundingClientRect();
    setMenu((current) => (
      current !== null && current.highlightId === highlight.id && current.rectOrder === rectOrder
        ? null
        : {
          highlightId: highlight.id,
          rectOrder,
          rect: buttonRect,
          left: buttonRect.left - containerRect.left,
          top: buttonRect.bottom - containerRect.top + 6,
          maxWidth: containerRect.width,
        }
    ));
  };

  const menuHighlight = menu === null
    ? undefined
    : highlights.find(({ id }) => id === menu.highlightId);

  return (
    <div ref={containerRef} className="annotation-overlay" role="group" aria-label="论文高亮">
      {highlights.map((highlight) => (
        <div key={highlight.id} className="annotation-overlay__highlight-group">
          {highlight.anchor.rects.map((rect) => (
            <button
              key={rect.order}
              type="button"
              className="annotation-overlay__highlight"
              data-color={highlight.color}
              style={normalizedBoxStyle(rect)}
              aria-label={`高亮：${highlight.anchor.quote}`}
              onClick={(event) => openMenu(highlight, rect.order, event.currentTarget)}
            />
          ))}
        </div>
      ))}
      {menu !== null && menuHighlight !== undefined ? (
        <div
          className="annotation-overlay__menu"
          role="menu"
          style={{
            left: Math.max(0, Math.min(menu.left, menu.maxWidth - MENU_WIDTH)),
            top: menu.top,
            width: MENU_WIDTH,
          }}
        >
          <div className="annotation-overlay__colors" role="group" aria-label="高亮颜色">
            {HIGHLIGHT_COLORS.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                role="menuitemradio"
                aria-checked={menuHighlight.color === value}
                className="annotation-overlay__color"
                data-color={value}
                aria-label={`${label}高亮`}
                disabled={onChangeColor === undefined}
                title={onChangeColor === undefined ? '暂不可用' : undefined}
                onClick={() => onChangeColor?.(menuHighlight.id, value)}
              />
            ))}
          </div>
          <button
            type="button"
            role="menuitem"
            disabled={onAddNote === undefined}
            title={onAddNote === undefined ? '暂不可用' : undefined}
            onClick={() => {
              onAddNote?.(menuHighlight, menu.rect);
              setMenu(null);
            }}
          >
            记笔记
          </button>
          <button type="button" role="menuitem" onClick={() => onDeleteHighlight(menuHighlight.id)}>
            取消高亮
          </button>
        </div>
      ) : null}
    </div>
  );
}
