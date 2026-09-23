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

it('shows an unavailable citation without exposing its raw identifier', () => {
  render(<MarkdownText text={'未知位置 [[element-unknown]] 的结论。'} citations={[citation]} />);

  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(screen.getByText('引用位置不可用')).toBeVisible();
  expect(screen.queryByText(/\[\[element-unknown\]\]/)).not.toBeInTheDocument();
});

it('renders GitHub-flavored pipe tables with a header and data rows', () => {
  const markdown = [
    '各专家的负载如下：',
    '',
    '| Expert | Tokens |',
    '| --- | --- |',
    '| FFN-A | 12.4 |',
    '| FFN-B | 9.8 |',
    '',
    '表格说明完毕。',
  ].join('\n');

  render(<MarkdownText text={markdown} />);

  const table = screen.getByRole('table');
  expect(table.querySelector('thead th')?.textContent).toBe('Expert');
  const headers = screen.getAllByRole('columnheader').map((node) => node.textContent);
  expect(headers).toEqual(['Expert', 'Tokens']);
  const cells = screen.getAllByRole('cell').map((node) => node.textContent);
  expect(cells).toEqual(['FFN-A', '12.4', 'FFN-B', '9.8']);
  expect(screen.getByText('表格说明完毕。')).toBeVisible();
});

it('unescapes escaped pipes inside table cells', () => {
  render(
    <MarkdownText
      text={'| 模型 | 说明 |\n| --- | --- |\n| A\\|B | 或语义 |'}
    />,
  );

  const cells = screen.getAllByRole('cell').map((node) => node.textContent);
  expect(cells).toEqual(['A|B', '或语义']);
});

it('falls back to a paragraph while a table is still streaming', () => {
  // Only the header row has arrived: no separator yet, so no half-baked table.
  render(<MarkdownText text={'| Expert | Tokens |'} />);

  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.getByText('| Expert | Tokens |')).toBeVisible();
});

it('renders inline and display LaTeX math with KaTeX', () => {
  render(<MarkdownText text={'损失为 $L_{total}$，推导如下。\n\n$$\\alpha + \\beta = 1$$'} />);

  expect(document.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2);
  expect(document.querySelector('.markdown-math-block .katex-display')).not.toBeNull();
});

it('renders a multi-line display math fence as one block', () => {
  render(<MarkdownText text={'$$\nE = mc^2\n$$'} />);

  expect(document.querySelector('.markdown-math-block .katex-display')).not.toBeNull();
});

it('keeps unclosed and currency dollars as literal text while streaming', () => {
  render(<MarkdownText text={'价格是 $5，梯度尚未闭合 $\\alpha'} />);

  expect(document.querySelector('.katex')).toBeNull();
  expect(screen.getByText(/价格是 \$5/)).toBeVisible();
});

it('keeps rendering citations inside table cells', async () => {
  const user = userEvent.setup();
  const onSelectCitation = vi.fn();
  render(
    <MarkdownText
      text={'| 结论 | 出处 |\n| --- | --- |\n| 路由有效 | [[element-a]] |'}
      citations={[citation]}
      onSelectCitation={onSelectCitation}
    />,
  );

  await user.click(screen.getByRole('button', { name: '第 2 页' }));

  expect(onSelectCitation).toHaveBeenCalledWith(citation);
});
