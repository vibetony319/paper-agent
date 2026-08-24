import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { NotesPanel } from './NotesPanel';

const source = {
  id: 'element-a', kind: 'paragraph', pageNumber: 2,
  bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
};

const element = {
  id: 'element-a', kind: 'paragraph', text: '定位原文', page_number: 2,
  bbox: source.bbox, section_id: null, location_status: 'located' as const, order: 1,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

afterEach(cleanup);

it('filters translated notes and positions an anchored note from its persisted source', async () => {
  const onSelectAnchor = vi.fn();
  render(
    <NotesPanel
      paperId="paper-a"
      activeSource={null}
      notes={[
        {
          id: 'translation-note',
          body: '这是翻译。',
          element_id: null,
          page_number: 2,
          note_type: 'translation',
          anchor_ids: ['anchor-a'],
          ai_generated: true,
          user_edited: false,
        },
      ]}
      anchors={[{
        id: 'anchor-a', quote: 'Original selected text', page_number: 2, element_id: null,
        rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 }],
      }]}
      documentElements={[]}
      saveNote={vi.fn()}
      onSelectSource={vi.fn()}
      onSelectAnchor={onSelectAnchor}
      updateNote={vi.fn()}
      deleteNote={vi.fn()}
    />,
  );

  await screen.findByRole('button', { name: '翻译' }).then((button) => button.click());
  expect(screen.getByText('这是翻译。')).toBeVisible();
  await screen.findByRole('button', { name: 'Original selected text' }).then((button) => button.click());
  expect(onSelectAnchor).toHaveBeenCalledWith(expect.objectContaining({
    id: 'anchor-a', pageNumber: 2, bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
  }));
});

it('creates a note for the active source once and de-duplicates the authoritative update', async () => {
  const user = userEvent.setup();
  const saved = { id: 'note-a', body: '记录这个假设。', element_id: 'element-a', page_number: 2 };
  const saveNote = vi.fn().mockResolvedValue(saved);
  const { rerender } = render(<NotesPanel paperId="paper-a" activeSource={source} notes={[]} anchors={[]} documentElements={[element]} saveNote={saveNote} onSelectSource={vi.fn()} />);

  await user.type(screen.getByLabelText('新建笔记'), saved.body);
  await user.click(screen.getByRole('button', { name: '保存笔记' }));
  await waitFor(() => expect(saveNote).toHaveBeenCalledWith(saved.body, 'element-a', 2));
  expect(screen.getAllByText(saved.body)).toHaveLength(1);

  rerender(<NotesPanel paperId="paper-a" activeSource={source} notes={[saved]} anchors={[]} documentElements={[element]} saveNote={saveNote} onSelectSource={vi.fn()} />);
  expect(screen.getAllByText(saved.body)).toHaveLength(1);
});

it('keeps an unbound note draft for retry when saving fails', async () => {
  const user = userEvent.setup();
  render(<NotesPanel paperId="paper-a" activeSource={null} notes={[]} anchors={[]} documentElements={[]} saveNote={vi.fn().mockResolvedValue(null)} onSelectSource={vi.fn()} />);

  await user.type(screen.getByLabelText('新建笔记'), '独立观察。');
  await user.click(screen.getByRole('button', { name: '保存笔记' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('笔记保存失败，请稍后重试。');
  expect(screen.getByLabelText('新建笔记')).toHaveValue('独立观察。');
});

it('does not show a stale paper A save failure after switching to paper B', async () => {
  const user = userEvent.setup();
  const pendingSave = deferred<null>();
  const { rerender } = render(<NotesPanel paperId="paper-a" activeSource={source} notes={[]} anchors={[]} documentElements={[element]} saveNote={vi.fn().mockReturnValue(pendingSave.promise)} onSelectSource={vi.fn()} />);

  await user.type(screen.getByLabelText('新建笔记'), '论文 A 笔记');
  await user.click(screen.getByRole('button', { name: '保存笔记' }));
  rerender(<NotesPanel paperId="paper-b" activeSource={null} notes={[]} anchors={[]} documentElements={[]} saveNote={vi.fn()} onSelectSource={vi.fn()} />);
  await act(async () => { pendingSave.resolve(null); await pendingSave.promise; });

  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByLabelText('新建笔记')).toHaveValue('');
});

it('shows notes newest first and keeps their original order when timestamps match or are absent', () => {
  render(
    <NotesPanel
      paperId="paper-a"
      activeSource={null}
      notes={[
        { id: 'same-first', body: '同一时间第一条', element_id: null, page_number: 1, created_at: '2026-08-21T10:00:00Z' },
        { id: 'older', body: '较早笔记', element_id: null, page_number: 1, created_at: '2026-08-20T10:00:00Z' },
        { id: 'same-second', body: '同一时间第二条', element_id: null, page_number: 1, created_at: '2026-08-21T10:00:00Z' },
        { id: 'unknown', body: '无时间笔记', element_id: null, page_number: 1 },
      ]}
      anchors={[]}
      documentElements={[]}
      saveNote={vi.fn()}
      onSelectSource={vi.fn()}
    />,
  );

  expect(screen.getAllByRole('article').map((note) => note.textContent)).toEqual([
    expect.stringContaining('同一时间第一条'),
    expect.stringContaining('同一时间第二条'),
    expect.stringContaining('较早笔记'),
    expect.stringContaining('无时间笔记'),
  ]);
});
