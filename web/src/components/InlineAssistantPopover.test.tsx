import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

import { InlineAssistantPopover } from './InlineAssistantPopover';

const draft = {
  quote: 'routing mechanism',
  page_number: 1,
  rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.7, y1: 0.3 }],
};

class ResizeObserverStub {
  static instances: ResizeObserverStub[] = [];
  readonly disconnect = vi.fn();
  readonly observe = vi.fn();

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverStub.instances.push(this);
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  ResizeObserverStub.instances.splice(0);
});

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

it('shows that the answer is being generated before the first delta lands', async () => {
  // Breaks if an empty popover reads as "the button did nothing".
  const runSelectionAssist = vi.fn(() => new Promise<never>(() => {}));
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
  expect(screen.getByText('正在生成解释…')).toBeVisible();
  expect(screen.getByRole('button', { name: '取消' })).toBeVisible();
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

it('reclamps a tall completed response after its popover grows near the viewport edge', async () => {
  let popoverHeight = 120;
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(800);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function getBoundingClientRect(this: HTMLElement) {
    if (this.classList.contains('inline-assistant-popover')) {
      return { width: 320, height: popoverHeight } as DOMRect;
    }
    return { width: 0, height: 0 } as DOMRect;
  });
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 600, top: 300, width: 40, height: 20 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '很长的完成结果。' })}
      onDismiss={vi.fn()}
    />,
  );

  const popover = await screen.findByLabelText('解释选区');
  await screen.findByText('很长的完成结果。');
  expect(ResizeObserverStub.instances).toHaveLength(1);
  popoverHeight = 560;
  await act(async () => {
    ResizeObserverStub.instances[0]?.callback([], ResizeObserverStub.instances[0] as never);
  });

  expect(popover).toHaveStyle({ left: '468px', top: '28px' });
});

it('reclamps its placement when the viewport resizes', async () => {
  let viewportWidth = 800;
  let viewportHeight = 600;
  vi.spyOn(window, 'innerWidth', 'get').mockImplementation(() => viewportWidth);
  vi.spyOn(window, 'innerHeight', 'get').mockImplementation(() => viewportHeight);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function getBoundingClientRect(this: HTMLElement) {
    if (this.classList.contains('inline-assistant-popover')) {
      return { width: 320, height: 180 } as DOMRect;
    }
    return { width: 0, height: 0 } as DOMRect;
  });
  render(
    <InlineAssistantPopover
      action="translate"
      draft={draft}
      toolbarRect={{ left: 600, top: 100, width: 40, height: 10 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '翻译完成。' })}
      onDismiss={vi.fn()}
    />,
  );

  const popover = await screen.findByLabelText('翻译选区');
  viewportWidth = 340;
  viewportHeight = 300;
  fireEvent(window, new Event('resize'));

  await waitFor(() => expect(popover).toHaveStyle({ left: '12px', top: '108px' }));
});

it('keeps the same idempotency key when retrying one popover request', async () => {
  vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'assist-request-id') });
  const runSelectionAssist = vi.fn()
    .mockResolvedValueOnce({ status: 'failed', text: '', message: '解释失败，请重试。' })
    .mockResolvedValueOnce({ status: 'completed', text: '重试结果。' });
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

  await screen.findByRole('button', { name: '重试' });
  await user.click(screen.getByRole('button', { name: '重试' }));
  await waitFor(() => expect(runSelectionAssist).toHaveBeenCalledTimes(2));
  expect(runSelectionAssist.mock.calls.map((call) => call[3])).toEqual([
    'assist-request-id', 'assist-request-id',
  ]);
});
