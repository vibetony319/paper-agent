import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { AnnotationOverlay } from './AnnotationOverlay';

afterEach(cleanup);

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
  const onChangeColor = vi.fn();
  render(
    <AnnotationOverlay
      highlights={[highlight]}
      onAddNote={onAddNote}
      onDeleteHighlight={onDeleteHighlight}
      onChangeColor={onChangeColor}
    />,
  );

  const overlay = screen.getByRole('button', { name: '高亮：论文片段' });
  expect(screen.getByRole('group', { name: '论文高亮' })).toContainElement(overlay);
  expect(overlay).toHaveStyle({ left: '10%', top: '20%', width: '50%', height: '5%' });
  fireEvent.click(overlay);
  expect(screen.getAllByRole('menuitem').map((item) => item.textContent))
    .toEqual(['记笔记', '取消高亮']);
  expect(screen.getByRole('group', { name: '高亮颜色' })).toBeInTheDocument();

  fireEvent.click(screen.getByRole('menuitemradio', { name: '蓝色高亮' }));
  expect(onChangeColor).toHaveBeenCalledWith('highlight-a', 'blue');

  fireEvent.click(screen.getByRole('menuitem', { name: '取消高亮' }));
  expect(onDeleteHighlight).toHaveBeenCalledWith('highlight-a');
});

it('marks the current color as checked in the menu', () => {
  render(
    <AnnotationOverlay
      highlights={[highlight]}
      onDeleteHighlight={vi.fn()}
      onChangeColor={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '高亮：论文片段' }));

  expect(screen.getByRole('menuitemradio', { name: '黄色高亮' })).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByRole('menuitemradio', { name: '绿色高亮' })).toHaveAttribute('aria-checked', 'false');
});

it('marks unwired highlight actions unavailable while retaining removal', () => {
  render(<AnnotationOverlay highlights={[highlight]} onDeleteHighlight={vi.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: '高亮：论文片段' }));
  expect(screen.getByRole('menuitem', { name: '记笔记' })).toBeDisabled();
  expect(screen.getByRole('menuitem', { name: '记笔记' })).toHaveAttribute('title', '暂不可用');
  expect(screen.getByRole('menuitemradio', { name: '黄色高亮' })).toBeDisabled();
  expect(screen.getByRole('menuitem', { name: '取消高亮' })).toBeEnabled();
});

it('closes the menu on Escape and outside pointer presses', () => {
  render(
    <AnnotationOverlay
      highlights={[highlight]}
      onDeleteHighlight={vi.fn()}
      onChangeColor={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: '高亮：论文片段' }));
  expect(screen.getByRole('menu')).toBeInTheDocument();

  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '高亮：论文片段' }));
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
});

it('toggles the menu when the same highlight rectangle is clicked twice', () => {
  render(<AnnotationOverlay highlights={[highlight]} onDeleteHighlight={vi.fn()} />);

  const overlay = screen.getByRole('button', { name: '高亮：论文片段' });
  fireEvent.click(overlay);
  expect(screen.getByRole('menu')).toBeInTheDocument();
  fireEvent.click(overlay);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
});

it('renders each highlight with its own color', () => {
  const green = {
    ...highlight,
    id: 'highlight-b',
    color: 'green' as const,
    anchor: { ...highlight.anchor, id: 'anchor-b', quote: '绿色选段' },
  };
  render(<AnnotationOverlay highlights={[highlight, green]} onDeleteHighlight={vi.fn()} />);

  expect(screen.getByRole('button', { name: '高亮：论文片段' })).toHaveAttribute('data-color', 'yellow');
  expect(screen.getByRole('button', { name: '高亮：绿色选段' })).toHaveAttribute('data-color', 'green');
});
