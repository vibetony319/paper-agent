import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import type { Citation } from '../api/types';
import { MarkdownText } from './MarkdownText';

afterEach(cleanup);

const citation: Citation = {
  id: 'element-a',
  kind: 'paragraph',
  page_number: 2,
  bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
};

it('renders headings, lists, emphasis and inline code from the model markdown', () => {
  render(<MarkdownText text={'## 方法\n\n- 使用 **路由损失**\n- 变量 `x`\n\n结论如下。'} />);

  expect(screen.getByRole('heading', { level: 4 })).toHaveTextContent('方法');
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
  expect(screen.getByText('路由损失').tagName).toBe('STRONG');
  expect(screen.getByText('x').tagName).toBe('CODE');
  expect(screen.getByText('结论如下。')).toBeVisible();
});

it('turns known citation markers into readable page buttons', async () => {
  const user = userEvent.setup();
  const onSelectCitation = vi.fn();
  render(
    <MarkdownText
      text={'论文使用路由损失 [[element-a]] 分配专家。'}
      citations={[citation]}
      onSelectCitation={onSelectCitation}
    />,
  );

  await user.click(screen.getByRole('button', { name: '第 2 页' }));

  expect(onSelectCitation).toHaveBeenCalledWith(citation);
  expect(screen.queryByText('[[element-a]]')).not.toBeInTheDocument();
});

it('keeps an unknown citation marker as plain text instead of a dead button', () => {
  render(<MarkdownText text={'未知位置 [[element-unknown]] 的结论。'} citations={[citation]} />);

  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.getByText(/\[\[element-unknown\]\]/)).toBeVisible();
});
