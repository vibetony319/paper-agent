import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

import { InlineAssistantPopover } from './InlineAssistantPopover';

const draft = {
  quote: 'routing mechanism',
  page_number: 1,
  rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.7, y1: 0.3 }],
};

afterEach(cleanup);

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

  await waitFor(() => expect(runSelectionAssist).toHaveBeenCalledOnce());
  await user.click(screen.getByRole('button', { name: '取消' }));
  await waitFor(() => expect(abort).toHaveBeenCalledOnce());
  expect(screen.getByText('这是部分解释。')).toBeVisible();
  expect(screen.getByRole('button', { name: '复制' })).toBeEnabled();
});

it('starts once on mount and aborts an active request when the popover unmounts', async () => {
  const abort = vi.fn();
  const runSelectionAssist = vi.fn((_action, _draft, _model, _requestId, signal) => new Promise<{ status: 'cancelled'; text: string }>(() => {
    signal.addEventListener('abort', abort);
  }));
  const { unmount } = render(<InlineAssistantPopover action="translate" draft={draft} toolbarRect={{ left: 1, top: 1, width: 2, height: 2 } as DOMRect} modelProfileId="model-a" runSelectionAssist={runSelectionAssist} onDismiss={vi.fn()} />);

  await waitFor(() => expect(runSelectionAssist).toHaveBeenCalledOnce());
  unmount();
  expect(abort).toHaveBeenCalledOnce();
});
