import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';

import { ResizableSplit } from './ResizableSplit';

afterEach(cleanup);

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
});

it('uses a validated stored width, clamps pointer and keyboard adjustments, and persists the result', () => {
  localStorage.setItem('paper-agent:reader-split', '90');
  render(<ResizableSplit paper={<div>论文内容</div>} tools={<div>工具内容</div>} />);
  const separator = screen.getByRole('separator', { name: '调整论文与工具宽度' });
  const split = screen.getByTestId('resizable-split');
  expect(screen.getByText('论文内容').parentElement).toHaveClass('resizable-split__paper');
  expect(screen.getByText('工具内容').parentElement).toHaveClass('resizable-split__tools');
  Object.defineProperty(split, 'getBoundingClientRect', { value: () => ({ left: 0, width: 1000 }) });

  expect(split).toHaveStyle({ '--reader-split': '78%' });
  fireEvent.pointerDown(separator, { pointerId: 1, clientX: 100 });
  fireEvent.pointerMove(separator, { pointerId: 1, clientX: 100 });
  fireEvent.pointerUp(separator, { pointerId: 1, clientX: 100 });
  expect(split).toHaveStyle({ '--reader-split': '45%' });

  fireEvent.keyDown(separator, { key: 'ArrowRight' });
  expect(split).toHaveStyle({ '--reader-split': '47%' });
  expect(localStorage.getItem('paper-agent:reader-split')).toBe('47');
});

it('switches paper and tools below 880px without unmounting either subtree', () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 });
  render(<ResizableSplit paper={<input aria-label="论文状态" defaultValue="保留" />} tools={<input aria-label="工具状态" defaultValue="保留" />} />);

  expect(screen.queryByRole('separator')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '论文' })).toBeVisible();
  expect(screen.getByLabelText('论文状态')).toHaveValue('保留');
  fireEvent.click(screen.getByRole('button', { name: '工具' }));
  expect(screen.getByLabelText('工具状态')).toHaveValue('保留');
  expect(screen.getByLabelText('论文状态')).toBeInTheDocument();
});
