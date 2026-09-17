import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import type { AgentStreamStep } from '../api/types';
import { AgentStepsTimeline } from './AgentStepsTimeline';

afterEach(cleanup);

it('labels every step kind of the execution path', () => {
  const steps: AgentStreamStep[] = [
    { kind: 'notes', count: 3 },
    { kind: 'round', round: 1 },
    { kind: 'reasoning', round: 1, text: '先检索再回答。' },
    { kind: 'tool_call', round: 1, tool_name: 'search_paper', arguments: { query: 'router', limit: 5 } },
    { kind: 'tool_result', round: 1, tool_name: 'search_paper', evidence_count: 4 },
    { kind: 'compaction' },
    { kind: 'final_answer' },
  ];
  render(<AgentStepsTimeline steps={steps} />);

  expect(screen.getByText('检索到 3 条相关笔记')).toBeVisible();
  expect(screen.getByText('第 1 轮推理')).toBeVisible();
  expect(screen.getByText('思考过程')).toBeVisible();
  expect(screen.getByText('先检索再回答。')).toBeInTheDocument();
  expect(screen.getByText('调用检索论文内容：router')).toBeVisible();
  expect(screen.getByText('获得 4 条证据')).toBeVisible();
  expect(screen.getByText('压缩对话上下文')).toBeVisible();
  expect(screen.getByText('正在生成最终回答')).toBeVisible();
});

it('reports failed tool calls and falls back to the raw tool name', () => {
  const steps: AgentStreamStep[] = [
    { kind: 'notes', count: 0 },
    { kind: 'tool_call', round: 2, tool_name: 'mystery_tool' },
    { kind: 'tool_result', round: 2, tool_name: 'mystery_tool', error: '未知章节' },
  ];
  render(<AgentStepsTimeline steps={steps} />);

  expect(screen.getByText('检索相关笔记')).toBeVisible();
  expect(screen.getByText('调用mystery_tool')).toBeVisible();
  expect(screen.getByText('调用失败：未知章节')).toBeVisible();
});

it('renders nothing without steps', () => {
  const { container } = render(<AgentStepsTimeline steps={[]} />);
  expect(container).toBeEmptyDOMElement();
});
