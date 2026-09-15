import { useLayoutEffect, useRef, useState } from 'react';

import type { TextAnchorDraft } from '../api/types';

export type SelectionToolbarAction = 'explain' | 'translate' | 'note' | 'ask' | 'highlight';
export type SelectionToolbarHandlers = Partial<Record<SelectionToolbarAction, () => void>>;

type SelectionToolbarProps = {
  draft: TextAnchorDraft;
  toolbarRect: DOMRect;
  actions?: SelectionToolbarHandlers;
  onDismiss: () => void;
};

const toolbarActions: Array<{ action: SelectionToolbarAction; label: string }> = [
  { action: 'explain', label: '解释' },
  { action: 'translate', label: '翻译' },
  { action: 'note', label: '记笔记' },
  { action: 'ask', label: '问助手' },
  { action: 'highlight', label: '高亮' },
];

export function SelectionToolbar({ draft, toolbarRect, actions = {}, onDismiss }: SelectionToolbarProps) {
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 12 });

  useLayoutEffect(() => {
    const reposition = () => {
      const toolbar = toolbarRef.current;
      if (toolbar === null) return;
      const bounds = toolbar.getBoundingClientRect();
      const width = bounds.width;
      const height = bounds.height;
      const left = Math.min(
        Math.max(12, toolbarRect.left + toolbarRect.width / 2 - width / 2),
        Math.max(12, window.innerWidth - width - 12),
      );
      const top = Math.min(
        Math.max(12, toolbarRect.top - height - 8),
        Math.max(12, window.innerHeight - height - 12),
      );
      setPosition({ left, top });
    };
    reposition();
    window.addEventListener('resize', reposition);
    return () => window.removeEventListener('resize', reposition);
  }, [toolbarRect]);

  return (
    <div
      ref={toolbarRef}
      className="selection-toolbar"
      role="toolbar"
      aria-label={`已选择：${draft.quote}`}
      style={position}
      // Pressing a button would otherwise collapse the reader selection, which
      // clears the workspace selection and unmounts this toolbar before the
      // click is delivered. Preventing the collapse keeps the selection (and the
      // toolbar) intact, and the pointerup is kept off the reader so its own
      // selection capture cannot clear anything either.
      onMouseDown={(event) => event.preventDefault()}
      onPointerUp={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          onDismiss();
        }
      }}
    >
      {toolbarActions.map(({ action, label }) => {
        const handler = actions[action];
        return (
          <button
            key={action}
            type="button"
            disabled={handler === undefined}
            title={handler === undefined ? '暂不可用' : undefined}
            onClick={() => handler?.()}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
