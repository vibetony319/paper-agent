import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { AgentPanel } from './AgentPanel';

afterEach(cleanup);

const exchanges = [
  { question: '第一问', steps: [], message: { conversation_id: 'conversation-a', message_id: 'answer-1', status: 'grounded' as const, paper_answer: '答案一 [[source-a]]', background_explanation: null, citations: [{ id: 'source-a', kind: 'paragraph', page_number: 2, bbox: { x0: 0.1, y0: 0.2, x1: 0.4, y1: 0.3 } }] } },
  { question: '第二问', steps: [], message: { conversation_id: 'conversation-a', message_id: 'answer-2', status: 'insufficient_evidence' as const, paper_answer: '答案二', background_explanation: null, citations: [] } },
];

it('shows conversation history and jumps to an indexed turn', async () => {
  const user = userEvent.setup();
  const onSelectConversation = vi.fn();
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
  render(<AgentPanel paperId="paper-a" exchanges={exchanges} onSelectCitation={vi.fn()} conversations={[{ id: 'conversation-a', first_question: '第一问' }]} conversationId="conversation-a" onSelectConversation={onSelectConversation} />);

  expect(screen.getByLabelText('对话记录')).toHaveValue('conversation-a');
  await user.click(screen.getByText('对话索引 · 2 轮'));
  await user.click(screen.getByRole('button', { name: '第 1 轮 · 第一问' }));
  expect(scrollIntoView).toHaveBeenCalledWith({ block: 'start', behavior: 'smooth' });
  await user.click(screen.getByRole('button', { name: '新建对话' }));
  expect(onSelectConversation).toHaveBeenCalledWith(null);
});

it('adds an answer to notes with readable citation text', async () => {
  const user = userEvent.setup();
  const onAddToNotes = vi.fn().mockResolvedValue({ id: 'note-a' });
  render(<AgentPanel paperId="paper-a" exchanges={[exchanges[0]]} onSelectCitation={vi.fn()} onAddToNotes={onAddToNotes} />);
  await user.click(screen.getByRole('button', { name: '添加到笔记' }));
  expect(onAddToNotes).toHaveBeenCalledWith('答案一 （论文第 2 页）');
  expect(await screen.findByRole('button', { name: '已添加到笔记' })).toBeDisabled();
});
