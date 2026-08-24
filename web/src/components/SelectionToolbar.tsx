import { useLayoutEffect, useRef, useState } from 'react';

import type { TextAnchorDraft } from '../api/types';

export type SelectionToolbarAction = 'explain' | 'translate' | 'note' | 'ask' | 'highlight';

type SelectionToolbarProps = {
  draft: TextAnchorDraft;
  toolbarRect: DOMRect;
  onAction: (action: SelectionToolbarAction) => void;
  onDismiss: () => void;
};

const actions: Array<{ action: SelectionToolbarAction; label: string }> = [
  { action: 'explain', label: '解释' },
  { action: 'translate', label: '翻译' },
  { action: 'note', label: '记笔记' },
  { action: 'ask', label: '问助手' },
  { action: 'highlight', label: '高亮' },
];

export function SelectionToolbar({ draft, toolbarRect, onAction, onDismiss }: SelectionToolbarProps) {
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 12 });

  useLayoutEffect(() => {
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
  }, [toolbarRect]);

  return (
    <div
      ref={toolbarRef}
      className="selection-toolbar"
      role="toolbar"
      aria-label={`已选择：${draft.quote}`}
      style={position}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          onDismiss();
        }
      }}
    >
      {actions.map(({ action, label }) => (
        <button key={action} type="button" onClick={() => onAction(action)}>{label}</button>
      ))}
    </div>
  );
}
