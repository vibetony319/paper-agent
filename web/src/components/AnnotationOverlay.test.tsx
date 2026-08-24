import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { AnnotationOverlay } from './AnnotationOverlay';

const highlight = {
  id: 'highlight-a',
  color: 'yellow' as const,
  anchor: {
    id: 'anchor-a',
    quote: '论文片段',
    page_number: 2,
    element_id: null,
    rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.25 }],
  },
};

it('opens the required highlight menu and routes its actions', () => {
  const onAddNote = vi.fn();
  const onDeleteHighlight = vi.fn();
  render(
    <AnnotationOverlay
      highlights={[highlight]}
      onAddNote={onAddNote}
      onDeleteHighlight={onDeleteHighlight}
    />,
  );

  const overlay = screen.getByRole('button', { name: '高亮：论文片段' });
  expect(overlay).toHaveStyle({ left: '10%', top: '20%', width: '50%', height: '5%' });
  fireEvent.click(overlay);
  expect(screen.getAllByRole('menuitem').map((item) => item.textContent))
    .toEqual(['记笔记', '删除高亮']);
  fireEvent.click(screen.getByRole('menuitem', { name: '删除高亮' }));
  expect(onDeleteHighlight).toHaveBeenCalledWith('highlight-a');
});
