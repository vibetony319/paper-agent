import { useLayoutEffect, useState } from 'react';

import type { TextAnchorDraft } from '../api/types';

export function ManualNotePopover({
  draft, toolbarRect, onSave, onDismiss,
}: {
  draft: TextAnchorDraft;
  toolbarRect: DOMRect;
  onSave: (body: string, anchor: TextAnchorDraft) => Promise<unknown>;
  onDismiss: () => void;
}) {
  const [body, setBody] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [position, setPosition] = useState({ left: toolbarRect.left, top: toolbarRect.top + toolbarRect.height + 8 });
  useLayoutEffect(() => setPosition({ left: Math.max(12, toolbarRect.left), top: Math.max(12, (toolbarRect.bottom || toolbarRect.top + toolbarRect.height) + 8) }), [toolbarRect]);
  return <form className="manual-note-popover" style={position} aria-label="为选区记笔记" onSubmit={(event) => {
    event.preventDefault();
    if (!body.trim() || pending) return;
    setPending(true); setError(null);
    void onSave(body.trim(), draft).then((note) => { if (note) onDismiss(); else setError('笔记保存失败，请稍后重试。'); })
      .catch(() => setError('笔记保存失败，请稍后重试。')).finally(() => setPending(false));
  }}>
    <label htmlFor="selection-note">选区笔记</label>
    <textarea id="selection-note" autoFocus rows={3} value={body} onChange={(event) => setBody(event.target.value)} />
    {error ? <p role="alert">{error}</p> : null}
    <div><button type="submit" disabled={!body.trim() || pending}>{pending ? '保存中…' : '保存'}</button><button type="button" onClick={onDismiss}>取消</button></div>
  </form>;
}
