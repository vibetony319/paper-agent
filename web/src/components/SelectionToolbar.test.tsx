import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { SelectionToolbar } from './SelectionToolbar';

afterEach(cleanup);

const draft = {
  quote: '选中的论文文字',
  page_number: 1,
  rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 }],
};

it('keeps the required action order, marks unwired actions unavailable, and dismisses on Escape', () => {
  const highlight = vi.fn();
  const onDismiss = vi.fn();
  render(
    <SelectionToolbar
      draft={draft}
      toolbarRect={{ left: 20, top: 100, width: 100, height: 20 } as DOMRect}
      actions={{ highlight }}
      onDismiss={onDismiss}
    />,
  );

  expect(screen.getAllByRole('button').map((button) => button.textContent))
    .toEqual(['解释', '翻译', '记笔记', '问助手', '高亮']);
  for (const label of ['解释', '翻译', '记笔记', '问助手']) {
    expect(screen.getByRole('button', { name: label })).toBeDisabled();
    expect(screen.getByRole('button', { name: label })).toHaveAttribute('title', '暂不可用');
  }
  expect(screen.getByRole('button', { name: '高亮' })).toBeEnabled();
  fireEvent.keyDown(screen.getByRole('toolbar'), { key: 'Escape' });
  expect(onDismiss).toHaveBeenCalledOnce();
});

it('recomputes its fixed position when the viewport changes', () => {
  render(
    <SelectionToolbar
      draft={draft}
      toolbarRect={{ left: 950, top: 100, width: 100, height: 20 } as DOMRect}
      actions={{ highlight: vi.fn() }}
      onDismiss={vi.fn()}
    />,
  );
  const toolbar = screen.getByRole('toolbar');
  Object.defineProperty(toolbar, 'getBoundingClientRect', { configurable: true, value: () => ({ width: 220, height: 40 }) });
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
  fireEvent(window, new Event('resize'));

  expect(toolbar).toHaveStyle({ left: '768px' });
});
