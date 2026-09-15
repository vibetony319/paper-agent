import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { afterEach, expect, it, vi } from 'vitest';

import type { PaperSummary } from '../api/types';
import { server } from '../test/server';
import { ConfirmDeleteDialog } from './ConfirmDeleteDialog';

const paper: PaperSummary = {
  id: 'paper-a',
  original_filename: 'routing-paper.pdf',
  status: 'completed',
  stage0_status: 'completed',
  stage1_status: 'completed',
  error: null,
};

afterEach(cleanup);

it('renders nothing without a target paper', () => {
  const { container } = render(
    <ConfirmDeleteDialog paper={null} onCancel={vi.fn()} onDeleted={vi.fn()} />,
  );

  expect(container).toBeEmptyDOMElement();
});

it('lists the deleted data and confirms permanent deletion with the paper confirmation', async () => {
  const user = userEvent.setup();
  const onDeleted = vi.fn();
  let deleteBody: unknown = null;
  server.use(http.delete('/api/papers/paper-a', async ({ request }) => {
    deleteBody = await request.json();
    return new HttpResponse(null, { status: 204 });
  }));

  render(<ConfirmDeleteDialog paper={paper} onCancel={vi.fn()} onDeleted={onDeleted} />);

  expect(screen.getByRole('dialog', { name: '删除论文' })).toBeVisible();
  expect(screen.getByText(/routing-paper\.pdf/)).toBeVisible();
  expect(screen.getByText('PDF、解析结果、高亮、笔记和对话将被永久删除。')).toBeVisible();

  await user.click(screen.getByRole('button', { name: '确认永久删除' }));

  await waitFor(() => expect(onDeleted).toHaveBeenCalledWith('paper-a'));
  expect(deleteBody).toEqual({ confirmation: 'paper-a' });
});

it('cancels without sending a delete request', async () => {
  const user = userEvent.setup();
  const onCancel = vi.fn();
  let deleteRequests = 0;
  server.use(http.delete('/api/papers/paper-a', () => {
    deleteRequests += 1;
    return new HttpResponse(null, { status: 204 });
  }));

  render(<ConfirmDeleteDialog paper={paper} onCancel={onCancel} onDeleted={vi.fn()} />);

  await user.click(screen.getByRole('button', { name: '取消' }));

  expect(onCancel).toHaveBeenCalledOnce();
  expect(deleteRequests).toBe(0);
});

it('treats the escape cancel as a cancel only', async () => {
  const onCancel = vi.fn();
  let deleteRequests = 0;
  server.use(http.delete('/api/papers/paper-a', () => {
    deleteRequests += 1;
    return new HttpResponse(null, { status: 204 });
  }));

  render(<ConfirmDeleteDialog paper={paper} onCancel={onCancel} onDeleted={vi.fn()} />);

  fireEvent(screen.getByRole('dialog', { name: '删除论文' }), new Event('cancel', {
    cancelable: true,
  }));

  expect(onCancel).toHaveBeenCalledOnce();
  expect(deleteRequests).toBe(0);
});

it('keeps the dialog open with the chinese error after a busy conflict', async () => {
  const user = userEvent.setup();
  const onDeleted = vi.fn();
  server.use(http.delete('/api/papers/paper-a', () => HttpResponse.json(
    { code: 'PAPER_BUSY', detail: '内部错误：Paper is busy at 10.0.0.7' },
    { status: 409 },
  )));

  render(<ConfirmDeleteDialog paper={paper} onCancel={vi.fn()} onDeleted={onDeleted} />);

  await user.click(screen.getByRole('button', { name: '确认永久删除' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('论文正在处理中，请稍后重试。');
  expect(screen.getByRole('dialog', { name: '删除论文' })).toBeVisible();
  expect(onDeleted).not.toHaveBeenCalled();
});

it('falls back to a public-safe message for non-api failures', async () => {
  const user = userEvent.setup();
  server.use(http.delete('/api/papers/paper-a', () => HttpResponse.error()));

  render(<ConfirmDeleteDialog paper={paper} onCancel={vi.fn()} onDeleted={vi.fn()} />);

  await user.click(screen.getByRole('button', { name: '确认永久删除' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('删除失败，请重试。');
  expect(screen.getByRole('dialog', { name: '删除论文' })).toBeVisible();
});

it('disables the actions while deletion is in flight', async () => {
  const user = userEvent.setup();
  const onDeleted = vi.fn();
  let releaseDelete: (() => void) | undefined;
  server.use(http.delete('/api/papers/paper-a', async () => {
    await new Promise<void>((resolve) => {
      releaseDelete = resolve;
    });
    return new HttpResponse(null, { status: 204 });
  }));

  render(<ConfirmDeleteDialog paper={paper} onCancel={vi.fn()} onDeleted={onDeleted} />);

  await user.click(screen.getByRole('button', { name: '确认永久删除' }));

  expect(screen.getByRole('button', { name: '正在删除…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();

  releaseDelete?.();
  await waitFor(() => expect(onDeleted).toHaveBeenCalledWith('paper-a'));
});
