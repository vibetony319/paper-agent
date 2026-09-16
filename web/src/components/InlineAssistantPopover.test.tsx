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

const SIZE_STORAGE_KEY = 'paper-agent:assist-popover-size';

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
  localStorage.removeItem(SIZE_STORAGE_KEY);
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

it('drags the popover by its header and keeps it inside the viewport', async () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(800);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(600);
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 100, top: 190, width: 40, height: 10 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '完成。' })}
      onDismiss={vi.fn()}
    />,
  );

  const popover = await screen.findByLabelText('解释选区');
  const header = screen.getByLabelText('拖拽移动解释弹窗');
  expect(popover).toHaveStyle({ left: '100px', top: '208px' });

  fireEvent.pointerDown(header, { pointerId: 1, button: 0, clientX: 150, clientY: 220 });
  fireEvent.pointerMove(header, { pointerId: 1, clientX: 300, clientY: 260 });
  expect(popover).toHaveStyle({ left: '250px', top: '248px' });

  fireEvent.pointerMove(header, { pointerId: 1, clientX: 900, clientY: 700 });
  fireEvent.pointerUp(header, { pointerId: 1, clientX: 900, clientY: 700 });
  expect(popover).toHaveStyle({ left: '788px', top: '588px' });
});

it('resizes the popover from the corner handle and persists the size', async () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(800);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function getBoundingClientRect(this: HTMLElement) {
    if (this.classList.contains('inline-assistant-popover')) {
      return { left: 100, top: 100, width: 320, height: 160 } as DOMRect;
    }
    return { width: 0, height: 0 } as DOMRect;
  });
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 100, top: 92, width: 40, height: 8 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '完成。' })}
      onDismiss={vi.fn()}
    />,
  );

  const popover = await screen.findByLabelText('解释选区');
  const handle = screen.getByRole('separator', { name: '调整解释弹窗大小' });

  fireEvent.pointerDown(handle, { pointerId: 2, button: 0, clientX: 420, clientY: 260 });
  fireEvent.pointerMove(handle, { pointerId: 2, clientX: 520, clientY: 460 });
  fireEvent.pointerUp(handle, { pointerId: 2, clientX: 520, clientY: 460 });
  expect(popover).toHaveClass('inline-assistant-popover--sized');
  expect(popover).toHaveStyle({ width: '420px', height: '360px' });

  fireEvent.pointerDown(handle, { pointerId: 3, button: 0, clientX: 420, clientY: 260 });
  fireEvent.pointerMove(handle, { pointerId: 3, clientX: 2000, clientY: 2000 });
  fireEvent.pointerUp(handle, { pointerId: 3, clientX: 2000, clientY: 2000 });
  expect(popover).toHaveStyle({ width: '776px', height: '576px' });
  expect(JSON.parse(localStorage.getItem(SIZE_STORAGE_KEY) ?? '{}')).toEqual({ width: 776, height: 576 });
});

it('nudges the popover with arrow keys from the header', async () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(800);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(600);
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 100, top: 190, width: 40, height: 10 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '完成。' })}
      onDismiss={vi.fn()}
    />,
  );

  const header = await screen.findByLabelText('拖拽移动解释弹窗');
  fireEvent.keyDown(header, { key: 'ArrowLeft' });
  fireEvent.keyDown(header, { key: 'ArrowDown', shiftKey: true });

  expect(screen.getByLabelText('解释选区')).toHaveStyle({ left: '92px', top: '232px' });
});

it('keeps a dragged position instead of re-anchoring when the viewport resizes', async () => {
  let viewportWidth = 800;
  let viewportHeight = 600;
  vi.spyOn(window, 'innerWidth', 'get').mockImplementation(() => viewportWidth);
  vi.spyOn(window, 'innerHeight', 'get').mockImplementation(() => viewportHeight);
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 600, top: 300, width: 40, height: 20 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '完成。' })}
      onDismiss={vi.fn()}
    />,
  );

  const popover = await screen.findByLabelText('解释选区');
  const header = screen.getByLabelText('拖拽移动解释弹窗');
  expect(popover).toHaveStyle({ left: '600px', top: '328px' });

  fireEvent.pointerDown(header, { pointerId: 1, button: 0, clientX: 600, clientY: 328 });
  fireEvent.pointerMove(header, { pointerId: 1, clientX: 200, clientY: 80 });
  fireEvent.pointerUp(header, { pointerId: 1, clientX: 200, clientY: 80 });
  expect(popover).toHaveStyle({ left: '200px', top: '80px' });

  // 150x100 viewport clamps to 138x88; re-anchoring to the selection would also force top to 88.
  viewportWidth = 150;
  viewportHeight = 100;
  fireEvent(window, new Event('resize'));

  await waitFor(() => expect(popover).toHaveStyle({ left: '138px', top: '80px' }));
});

it('restores a stored size and ignores invalid persisted values', async () => {
  localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify({ width: 480, height: 320 }));
  const { unmount } = render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 20, top: 30, width: 80, height: 20 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '完成。' })}
      onDismiss={vi.fn()}
    />,
  );
  const popover = await screen.findByLabelText('解释选区');
  expect(popover).toHaveClass('inline-assistant-popover--sized');
  expect(popover).toHaveStyle({ width: '480px', height: '320px' });
  unmount();

  localStorage.setItem(SIZE_STORAGE_KEY, '{"width": 40');
  render(
    <InlineAssistantPopover
      action="explain"
      draft={draft}
      toolbarRect={{ left: 20, top: 30, width: 80, height: 20 } as DOMRect}
      modelProfileId="model-a"
      runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '完成。' })}
      onDismiss={vi.fn()}
    />,
  );
  const nextPopover = await screen.findByLabelText('解释选区');
  expect(nextPopover).not.toHaveClass('inline-assistant-popover--sized');
  expect(nextPopover).not.toHaveStyle({ width: '480px' });
});

it('does not let handle interactions bubble out of the popover', async () => {
  const onPointerDown = vi.fn();
  const onPointerUp = vi.fn();
  render(
    <div onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
      <InlineAssistantPopover
        action="translate"
        draft={draft}
        toolbarRect={{ left: 100, top: 190, width: 40, height: 10 } as DOMRect}
        modelProfileId="model-a"
        runSelectionAssist={vi.fn().mockResolvedValue({ status: 'completed', text: '翻译完成。' })}
        onDismiss={vi.fn()}
      />
    </div>,
  );

  const header = await screen.findByLabelText('拖拽移动翻译弹窗');
  const handle = screen.getByRole('separator', { name: '调整翻译弹窗大小' });

  fireEvent.pointerDown(header, { pointerId: 1, button: 0, clientX: 150, clientY: 220 });
  fireEvent.pointerMove(header, { pointerId: 1, clientX: 200, clientY: 240 });
  fireEvent.pointerUp(header, { pointerId: 1, clientX: 200, clientY: 240 });
  fireEvent.pointerDown(handle, { pointerId: 2, button: 0, clientX: 400, clientY: 380 });
  fireEvent.pointerMove(handle, { pointerId: 2, clientX: 440, clientY: 400 });
  fireEvent.pointerUp(handle, { pointerId: 2, clientX: 440, clientY: 400 });

  expect(onPointerDown).not.toHaveBeenCalled();
  expect(onPointerUp).not.toHaveBeenCalled();
});
