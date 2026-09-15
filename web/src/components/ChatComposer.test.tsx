import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import type { AgentMessage, ModelProfile, TextAnchorDraft } from '../api/types';
import { ChatComposer } from './ChatComposer';

const profiles: ModelProfile[] = [
  {
    id: 'qwen', display_name: '本地 Qwen', base_url: 'http://localhost/v1', model_name: 'qwen3',
    enabled: true, is_default: true, revision: 1, has_api_key: false, api_key_mask: null,
    capabilities: { basic_chat: true, structured_output: true, tool_calling: false, checked_at: null }, read_only: false,
  },
  {
    id: 'deepseek', display_name: 'DeepSeek', base_url: 'http://localhost/v1', model_name: 'deepseek',
    enabled: true, is_default: false, revision: 1, has_api_key: false, api_key_mask: null,
    capabilities: { basic_chat: true, structured_output: true, tool_calling: false, checked_at: null }, read_only: false,
  },
];

const selection: TextAnchorDraft = {
  quote: '选择的论文原文', page_number: 2,
  rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }],
};

const response: AgentMessage = {
  conversation_id: 'conversation-a', message_id: 'message-a', status: 'grounded',
  paper_answer: '回答', background_explanation: null, citations: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

const firstAttachment = { draft: selection, token: 'attachment-a' };

afterEach(cleanup);

it('renders pending messages in the conversation and supports Enter and Shift+Enter', async () => {
  const user = userEvent.setup();
  const task = deferred<AgentMessage | null>();
  const target = document.createElement('div');
  document.body.append(target);
  const askAgent = vi.fn(() => task.promise);
  const view = render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen"
    onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={null}
    onAttachmentClear={vi.fn()} messageTarget={target} />);
  try {
    const input = screen.getByRole('textbox');
    await user.type(input, '解释方法');
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    expect(askAgent).not.toHaveBeenCalled();
    await user.type(input, '以及结论');
    await user.keyboard('{Enter}');
    expect(askAgent).toHaveBeenCalledWith('解释方法\n以及结论', 'qwen', undefined);
    expect(target).toHaveTextContent('正在生成回答');
    expect(screen.getByRole('form', { name: '论文助手输入区' })).not.toHaveTextContent('正在生成回答');
    await act(async () => task.resolve(response));
    expect(target).toBeEmptyDOMElement();
  } finally { view.unmount(); target.remove(); }
});

it('answers greetings locally and shows honest pending feedback for paper questions', async () => {
  const user = userEvent.setup();
  const task = deferred<AgentMessage | null>();
  const askAgent = vi.fn(() => task.promise);
  render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen"
    onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={null} onAttachmentClear={vi.fn()} />);
  const input = screen.getByRole('textbox');
  await user.type(input, '你好');
  await user.click(screen.getByRole('button', { name: '发送' }));
  expect(screen.getByText(/你好！我可以帮你概括论文/)).toBeVisible();
  expect(askAgent).not.toHaveBeenCalled();
  await user.type(input, '这篇论文讲了什么');
  await user.click(screen.getByRole('button', { name: '发送' }));
  expect(screen.getByText(/正在生成回答/)).toBeVisible();
  expect(input).toHaveValue('');
  expect(screen.getByText('这篇论文讲了什么')).toHaveClass('agent-panel__question');
  await act(async () => task.resolve(response));
  expect(screen.queryByText(/正在生成回答/)).not.toBeInTheDocument();
});

it('keeps an interrupted partial answer visible and can resend the question', async () => {
  // Breaks if a mid-stream failure throws away the text the reader was reading.
  const user = userEvent.setup();
  const askAgent = vi.fn().mockResolvedValue(null);
  const { rerender } = render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen"
    onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={null} onAttachmentClear={vi.fn()} />);

  await user.type(screen.getByLabelText('向论文助手提问'), '这篇论文讲了什么');
  await user.click(screen.getByRole('button', { name: '发送' }));
  await waitFor(() => expect(askAgent).toHaveBeenCalledOnce());

  rerender(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen"
    onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={null} onAttachmentClear={vi.fn()}
    streamingText={'论文提出了 InfoGain-RAG'} streamInterrupted />);

  expect(screen.getByText('论文提出了 InfoGain-RAG')).toBeVisible();
  expect(screen.getByText('回答已中断，以上为已生成的部分。')).toBeVisible();

  await user.click(screen.getByRole('button', { name: '重新提问' }));
  await waitFor(() => expect(askAgent).toHaveBeenCalledTimes(2));
  expect(askAgent).toHaveBeenLastCalledWith('这篇论文讲了什么', 'qwen', undefined);
});

it('sends the selected model and attached selection, then clears the attachment only after success', async () => {
  const user = userEvent.setup();
  const askAgent = vi.fn().mockResolvedValue(response);
  const onAttachmentClear = vi.fn();
  render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={firstAttachment} onAttachmentClear={onAttachmentClear} />);

  expect(screen.getByLabelText('已附加选区')).toHaveTextContent('选择的论文原文');
  await user.type(screen.getByLabelText('向论文助手提问'), '解释这一段');
  await user.click(screen.getByRole('button', { name: '发送' }));

  await waitFor(() => expect(askAgent).toHaveBeenCalledWith('解释这一段', 'qwen', selection));
  expect(onAttachmentClear).toHaveBeenCalledOnce();
  expect(onAttachmentClear).toHaveBeenCalledWith('attachment-a');
});

it('keeps the question and selection attachment for retry when sending fails and allows removing it', async () => {
  const user = userEvent.setup();
  const askAgent = vi.fn().mockResolvedValue(null);
  const onAttachmentClear = vi.fn();
  render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={firstAttachment} onAttachmentClear={onAttachmentClear} />);

  await user.type(screen.getByLabelText('向论文助手提问'), '解释这一段');
  await user.click(screen.getByRole('button', { name: '发送' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('发送失败，请重试。');
  expect(onAttachmentClear).not.toHaveBeenCalled();
  expect(screen.getByLabelText('向论文助手提问')).toHaveValue('解释这一段');

  await user.click(screen.getByRole('button', { name: '移除选区附件' }));
  expect(onAttachmentClear).toHaveBeenCalledWith('attachment-a');
});

it('does not clear a newer selection attachment when an earlier send succeeds', async () => {
  const user = userEvent.setup();
  const pendingRequest = deferred<AgentMessage | null>();
  const askAgent = vi.fn().mockReturnValue(pendingRequest.promise);
  const onAttachmentClear = vi.fn();
  const newerAttachment = {
    draft: { ...selection, quote: '新选择的论文原文' },
    token: 'attachment-b',
  };
  const { rerender } = render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={firstAttachment} onAttachmentClear={onAttachmentClear} />);

  await user.type(screen.getByLabelText('向论文助手提问'), '解释旧选区');
  await user.click(screen.getByRole('button', { name: '发送' }));
  expect(screen.getByRole('button', { name: '发送中…' })).toBeDisabled();
  rerender(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={newerAttachment} onAttachmentClear={onAttachmentClear} />);

  await act(async () => { pendingRequest.resolve(response); await pendingRequest.promise; });

  expect(screen.getByLabelText('已附加选区')).toHaveTextContent('新选择的论文原文');
  expect(onAttachmentClear).not.toHaveBeenCalled();
});

it('ignores a completed request from paper A after switching to paper B', async () => {
  const user = userEvent.setup();
  const pendingRequest = deferred<AgentMessage | null>();
  const askAgent = vi.fn().mockReturnValue(pendingRequest.promise);
  const onAttachmentClear = vi.fn();
  const paperBAttachment = { draft: { ...selection, quote: '论文 B 选区' }, token: 'attachment-b' };
  const { rerender } = render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={firstAttachment} onAttachmentClear={onAttachmentClear} />);

  await user.type(screen.getByLabelText('向论文助手提问'), '论文 A 问题');
  await user.click(screen.getByRole('button', { name: '发送' }));
  rerender(<ChatComposer paperId="paper-b" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={paperBAttachment} onAttachmentClear={onAttachmentClear} focusRequest={1} />);
  await user.type(screen.getByLabelText('向论文助手提问'), '论文 B 草稿');

  await act(async () => { pendingRequest.resolve(response); await pendingRequest.promise; });

  expect(screen.getByLabelText('向论文助手提问')).toHaveValue('论文 B 草稿');
  expect(screen.getByLabelText('已附加选区')).toHaveTextContent('论文 B 选区');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(onAttachmentClear).not.toHaveBeenCalled();
  expect(screen.getByLabelText('向论文助手提问')).toHaveFocus();
});

it('ignores a rejected request from paper A after switching to paper B', async () => {
  const user = userEvent.setup();
  const pendingRequest = deferred<AgentMessage | null>();
  const askAgent = vi.fn().mockReturnValue(pendingRequest.promise);
  const { rerender } = render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={firstAttachment} onAttachmentClear={vi.fn()} />);

  await user.type(screen.getByLabelText('向论文助手提问'), '论文 A 问题');
  await user.click(screen.getByRole('button', { name: '发送' }));
  rerender(<ChatComposer paperId="paper-b" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={null} onAttachmentClear={vi.fn()} />);
  await user.type(screen.getByLabelText('向论文助手提问'), '论文 B 草稿');

  await act(async () => { pendingRequest.reject(new Error('请求失败')); try { await pendingRequest.promise; } catch {} });

  expect(screen.getByLabelText('向论文助手提问')).toHaveValue('论文 B 草稿');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('does not start a duplicate send while the current request is pending', async () => {
  const user = userEvent.setup();
  const pendingRequest = deferred<AgentMessage | null>();
  const askAgent = vi.fn().mockReturnValue(pendingRequest.promise);
  render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} askAgent={askAgent} attachment={null} onAttachmentClear={vi.fn()} />);

  await user.type(screen.getByLabelText('向论文助手提问'), '不要重复发送');
  await user.click(screen.getByRole('button', { name: '发送' }));
  await user.click(screen.getByRole('button', { name: '发送中…' }));

  expect(askAgent).toHaveBeenCalledOnce();
});

it('uses the composer as the single model boundary and explains why sending is unavailable without one', async () => {
  const user = userEvent.setup();
  const onSelectedModelProfileIdChange = vi.fn();
  render(<ChatComposer paperId="paper-a" profiles={profiles} selectedModelProfileId={null} onSelectedModelProfileIdChange={onSelectedModelProfileIdChange} askAgent={vi.fn()} attachment={null} onAttachmentClear={vi.fn()} />);

  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
  expect(screen.getByText('请先在当前模型中选择可用模型，再发送问题。')).toBeVisible();

  await user.selectOptions(screen.getByLabelText('当前模型'), 'deepseek');
  expect(onSelectedModelProfileIdChange).toHaveBeenCalledWith('deepseek');
});
