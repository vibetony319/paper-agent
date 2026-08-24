import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { NotesPanel } from './NotesPanel';

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
