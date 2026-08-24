import { useEffect, useMemo, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import type { DocumentElement, Note, TextAnchor, TextAnchorDraft } from '../api/types';
import { hasValidSourceLocation, toAnchorSourceTarget } from '../workspace/sourceTarget';
import type { SourceTarget } from '../workspace/types';

export interface NotesPanelProps {
  paperId: string | null;
  activeSource: SourceTarget | null;
  notes: Note[];
  anchors?: TextAnchor[];
  documentElements: DocumentElement[];
  saveNote: (body: string, elementId?: string, pageNumber?: number, anchor?: TextAnchorDraft) => Promise<Note | null>;
  onSelectSource: (elementId: string) => void;
  onSelectAnchor?: (source: SourceTarget) => void;
  updateNote?: (noteId: string, body: string, expectedUpdatedAt?: string | null) => Promise<Note | null>;
  deleteNote?: (noteId: string) => Promise<boolean>;
}

type NoteFilter = 'all' | 'manual' | 'explanation' | 'translation';
const filters: Array<[NoteFilter, string]> = [['all', '全部'], ['manual', '我的笔记'], ['explanation', '解释'], ['translation', '翻译']];

export function NotesPanel({ paperId, activeSource, notes, anchors = [], documentElements, saveNote, onSelectSource, onSelectAnchor, updateNote, deleteNote }: NotesPanelProps) {
  const [filter, setFilter] = useState<NoteFilter>('all');
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [editBody, setEditBody] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [localNotes, setLocalNotes] = useState<Note[]>([]);
  const requestVersion = useRef(0);
  useEffect(() => {
    requestVersion.current += 1;
    setDraft(''); setEditing(null); setEditBody(''); setError(null); setPending(false); setLocalNotes([]);
  }, [paperId]);
  const elements = useMemo(() => new Map(documentElements.map((item) => [item.id, item])), [documentElements]);
  const anchorsById = useMemo(() => new Map(anchors.map((item) => [item.id, item])), [anchors]);
  const visible = useMemo(() => {
    const merged = new Map(notes.map((note) => [note.id, note])); localNotes.forEach((note) => merged.set(note.id, note));
    return [...merged.values()].filter((note) => filter === 'all' || (note.note_type ?? 'manual') === filter);
  }, [filter, localNotes, notes]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); if (!draft.trim() || pending || paperId === null) return;
    const version = ++requestVersion.current;
    const sourceAnchor = activeSource?.kind === 'text_anchor' ? anchorsById.get(activeSource.id) : undefined;
    setPending(true); setError(null); const note = sourceAnchor === undefined
      ? await saveNote(draft.trim(), activeSource?.id, activeSource?.pageNumber)
      : await saveNote(draft.trim(), undefined, activeSource?.pageNumber, { quote: sourceAnchor.quote, page_number: sourceAnchor.page_number, rects: sourceAnchor.rects, ...(sourceAnchor.element_id === null ? {} : { element_id: sourceAnchor.element_id }) });
    if (version !== requestVersion.current) return;
    setPending(false);
    if (!note) { setError('笔记保存失败，请稍后重试。'); return; }
    setLocalNotes((current) => [...current.filter(({ id }) => id !== note.id), note]); setDraft('');
  };
  return <section className="notes-panel" aria-labelledby="notes-panel-title">
    <header className="notes-panel__header"><div><p className="notes-panel__eyebrow">阅读笔记</p><h2 id="notes-panel-title">笔记</h2></div><p className="notes-panel__target">{activeSource ? `当前定位：第 ${activeSource.pageNumber} 页` : '未选择证据位置'}</p></header>
    <div className="notes-panel__filters" role="group" aria-label="笔记筛选">{filters.map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div>
    <form className="notes-panel__form" onSubmit={submit}><label htmlFor="new-note">新建笔记</label><textarea id="new-note" rows={3} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={pending || paperId === null} /><button type="submit" disabled={pending || !draft.trim() || paperId === null}>{pending ? '保存中…' : '保存笔记'}</button></form>
    {error ? <p className="notes-panel__error" role="alert">{error}</p> : null}
    <div className="notes-panel__list" aria-live="polite">{visible.length === 0 ? <p className="notes-panel__empty">暂无符合条件的笔记。</p> : visible.map((note) => {
      const anchor = note.anchor_ids?.map((id) => anchorsById.get(id)).find(Boolean); const source = anchor ? toAnchorSourceTarget(anchor) : null; const element = note.element_id ? elements.get(note.element_id) : undefined;
      const location = source ?? (element && hasValidSourceLocation(element) ? { id: element.id, kind: element.kind, pageNumber: element.page_number, bbox: element.bbox } : null);
      return <article className="notes-panel__note" key={note.id}>
        <button type="button" className="notes-panel__excerpt" aria-label={anchor?.quote ?? (location ? `定位到第 ${location.pageNumber} 页 ${location.kind}` : '原文位置不可用')} disabled={!location} onClick={() => { if (source) onSelectAnchor?.(source); else if (element) onSelectSource(element.id); }}>{anchor?.quote ?? (note.page_number ? `第 ${note.page_number} 页原文` : '未绑定原文')}</button>
        {editing === note.id ? <textarea aria-label="编辑笔记" value={editBody} onChange={(event) => setEditBody(event.target.value)} /> : <p>{note.body}</p>}
        <small>第 {anchor?.page_number ?? note.page_number ?? '—'} 页 · {note.note_type === 'translation' ? '翻译' : note.note_type === 'explanation' ? '解释' : '我的笔记'} · {note.model?.display_name ?? '手写'} · {note.user_edited ? '用户已编辑' : note.ai_generated ? 'AI生成' : '手写'}</small>
        <div>{editing === note.id ? <><button type="button" onClick={() => { void updateNote?.(note.id, editBody, note.updated_at).then((updated) => { if (updated) { setLocalNotes((items) => [...items.filter(({ id }) => id !== note.id), updated]); setEditing(null); } }).catch((reason) => setError(reason instanceof ApiError && reason.status === 409 ? '笔记已被更新，请刷新后再编辑。' : '笔记更新失败，请稍后重试。')); }}>保存修改</button><button type="button" onClick={() => setEditing(null)}>取消</button></> : <button type="button" onClick={() => { setEditing(note.id); setEditBody(note.body); }}>编辑</button>}<button type="button" onClick={() => { void navigator.clipboard?.writeText(note.body); }}>复制</button><button type="button" onClick={() => { void deleteNote?.(note.id).then((deleted) => { if (deleted) setLocalNotes((items) => items.filter(({ id }) => id !== note.id)); }); }}>删除</button></div>
      </article>;
    })}</div>
  </section>;
}
