import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

import { InlineAssistantPopover } from './InlineAssistantPopover';

const draft = {
  quote: 'routing mechanism',
  page_number: 1,
  rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.7, y1: 0.3 }],
};

it('keeps cancelled streamed text copyable without saving a partial note', async () => {
  const abort = vi.fn();
  let finish: ((value: { status: 'cancelled'; text: string }) => void) | undefined;
  const runSelectionAssist = vi.fn((_action, _draft, _modelId, _requestId, signal) => new Promise<{ status: 'cancelled'; text: string }>((resolve) => {
    signal.addEventListener('abort', () => { abort(); finish?.({ status: 'cancelled', text: '这是部分解释。' }); });
    finish = resolve;
  }));
  const user = userEvent.setup();
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 20, top: 30, width: 80, height: 20 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={runSelectionAssist}
      onDismiss={vi.fn()}
    />,
  );

  await user.click(screen.getByRole('button', { name: '开始解释' }));
  await user.click(screen.getByRole('button', { name: '取消' }));
  await waitFor(() => expect(abort).toHaveBeenCalledOnce());
  expect(screen.getByText('这是部分解释。')).toBeVisible();
  expect(screen.getByRole('button', { name: '复制' })).toBeEnabled();
});
