import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';

import { newRequestId } from '../api/ids';
import type { SelectionAssistAction, TextAnchorDraft } from '../api/types';

type AssistResult =
  | { status: 'completed' | 'cancelled'; text: string }
  | { status: 'failed'; text: string; message?: string };

export interface InlineAssistantPopoverProps {
  action: SelectionAssistAction;
  draft: TextAnchorDraft;
  toolbarRect: DOMRect;
  modelProfileId: string | null;
  runSelectionAssist: (
    action: SelectionAssistAction,
    draft: TextAnchorDraft,
    modelProfileId: string,
    requestId: string,
    signal: AbortSignal,
    onDelta?: (text: string) => void,
  ) => Promise<AssistResult>;
  onDismiss: () => void;
}

type PopoverState = 'idle' | 'streaming' | 'completed' | 'cancelled' | 'failed';

type PopoverPosition = { left: number; top: number };
type PopoverSize = { width: number; height: number };

const SIZE_STORAGE_KEY = 'paper-agent:assist-popover-size';
const VIEWPORT_MARGIN = 12;
const MIN_SIZE: PopoverSize = { width: 240, height: 160 };
const KEYBOARD_STEP = 8;
const KEYBOARD_STEP_LARGE = 24;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function clampToViewport(position: PopoverPosition, size: PopoverSize): PopoverPosition {
  return {
    left: clamp(position.left, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, window.innerWidth - size.width - VIEWPORT_MARGIN)),
    top: clamp(position.top, VIEWPORT_MARGIN, Math.max(VIEWPORT_MARGIN, window.innerHeight - size.height - VIEWPORT_MARGIN)),
  };
}

function loadStoredSize(): PopoverSize | null {
  const stored = localStorage.getItem(SIZE_STORAGE_KEY);
  if (stored === null) return null;
  try {
    const parsed = JSON.parse(stored) as { width?: unknown; height?: unknown };
    const { width, height } = parsed ?? {};
    if (typeof width !== 'number' || typeof height !== 'number') return null;
    if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
    if (width < MIN_SIZE.width || height < MIN_SIZE.height) return null;
    return { width: Math.round(width), height: Math.round(height) };
  } catch {
    return null;
  }
}

export function InlineAssistantPopover({
  action, draft, toolbarRect, modelProfileId, runSelectionAssist, onDismiss,
}: InlineAssistantPopoverProps) {
  const [state, setState] = useState<PopoverState>('idle');
  const [text, setText] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const requestId = useRef<string | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const userAdjusted = useRef(false);
  const dragState = useRef<{ pointerId: number; grabX: number; grabY: number } | null>(null);
  const resizeState = useRef<{ pointerId: number; startX: number; startY: number; width: number; height: number } | null>(null);
  const selectionBottom = toolbarRect.bottom || toolbarRect.top + toolbarRect.height;
  const [position, setPosition] = useState<PopoverPosition>({ left: toolbarRect.left, top: selectionBottom + 8 });
  const [size, setSize] = useState<PopoverSize | null>(loadStoredSize);
  const title = action === 'explain' ? '解释' : '翻译';

  const measuredSize = useCallback((): PopoverSize => {
    const bounds = popoverRef.current?.getBoundingClientRect();
    return { width: bounds?.width ?? 320, height: bounds?.height ?? 0 };
  }, []);

  const updatePosition = useCallback(() => {
    const bounds = measuredSize();
    if (userAdjusted.current) {
      // After the user moved the popover it stays where they put it; only keep it on screen.
      setPosition((current) => {
        const next = clampToViewport(current, bounds);
        return next.left === current.left && next.top === current.top ? current : next;
      });
      return;
    }
    const left = Math.min(Math.max(VIEWPORT_MARGIN, toolbarRect.left), Math.max(VIEWPORT_MARGIN, window.innerWidth - bounds.width - VIEWPORT_MARGIN));
    const top = Math.min(Math.max(VIEWPORT_MARGIN, selectionBottom + 8), Math.max(VIEWPORT_MARGIN, window.innerHeight - bounds.height - VIEWPORT_MARGIN));
    setPosition((current) => {
      if (current.left === left && current.top === top) return current;
      return { left, top };
    });
  }, [measuredSize, selectionBottom, toolbarRect.left]);

  useLayoutEffect(() => {
    updatePosition();
    const resizeObserver = typeof ResizeObserver === 'undefined'
      ? undefined
      : new ResizeObserver(updatePosition);
    if (popoverRef.current !== null) resizeObserver?.observe(popoverRef.current);
    window.addEventListener('resize', updatePosition);
    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener('resize', updatePosition);
    };
  }, [updatePosition]);

  useEffect(() => {
    if (size === null) return undefined;
    try { localStorage.setItem(SIZE_STORAGE_KEY, JSON.stringify(size)); } catch { /* storage unavailable */ }
    return undefined;
  }, [size]);

  const start = useCallback(async () => {
    if (modelProfileId === null || controller.current !== null) return;
    requestId.current ??= newRequestId();
    const nextController = new AbortController();
    controller.current = nextController;
    setState('streaming');
    setText('');
    setMessage(null);
    let result: AssistResult;
    try { result = await runSelectionAssist(
      action, draft, modelProfileId, requestId.current, nextController.signal,
      (delta) => setText((current) => current + delta),
    ); } catch {
      result = nextController.signal.aborted ? { status: 'cancelled', text: '' }
        : { status: 'failed', text: '', message: `${title}失败，请重试。` };
    }
    if (controller.current !== nextController) return;
    controller.current = null;
    setText(result.text);
    setState(result.status);
    setMessage(result.status === 'failed' ? result.message ?? `${title}失败，请重试。` : null);
  }, [action, draft, modelProfileId, runSelectionAssist, title]);

  useEffect(() => {
    if (modelProfileId === null) return undefined;
    // The timeout keeps StrictMode's development-only setup/cleanup cycle from starting a live request.
    const timer = window.setTimeout(() => { void start(); }, 0);
    return () => { window.clearTimeout(timer); const active = controller.current; controller.current = null; active?.abort(); };
  }, [action, draft, modelProfileId, start]);

  const onHeaderPointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest('button') !== null) return;
    event.stopPropagation();
    userAdjusted.current = true;
    dragState.current = {
      pointerId: event.pointerId,
      grabX: event.clientX - position.left,
      grabY: event.clientY - position.top,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const onHeaderPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragState.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    event.stopPropagation();
    const next = clampToViewport(
      { left: event.clientX - drag.grabX, top: event.clientY - drag.grabY },
      size ?? measuredSize(),
    );
    setPosition((current) => (current.left === next.left && current.top === next.top ? current : next));
  };

  const endDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragState.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    dragState.current = null;
    event.stopPropagation();
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const onHeaderKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const step = event.shiftKey ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP;
    const moves: Record<string, PopoverPosition> = {
      ArrowLeft: { left: -step, top: 0 },
      ArrowRight: { left: step, top: 0 },
      ArrowUp: { left: 0, top: -step },
      ArrowDown: { left: 0, top: step },
    };
    const move = moves[event.key];
    if (move === undefined) return;
    event.preventDefault();
    userAdjusted.current = true;
    const bounds = size ?? measuredSize();
    setPosition((current) => clampToViewport({ left: current.left + move.left, top: current.top + move.top }, bounds));
  };

  const onResizePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    userAdjusted.current = true;
    const current = size ?? measuredSize();
    resizeState.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      width: current.width,
      height: current.height,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const onResizePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const resize = resizeState.current;
    if (resize === null || resize.pointerId !== event.pointerId) return;
    event.stopPropagation();
    const next: PopoverSize = {
      width: clamp(resize.width + event.clientX - resize.startX, MIN_SIZE.width, Math.max(MIN_SIZE.width, window.innerWidth - VIEWPORT_MARGIN * 2)),
      height: clamp(resize.height + event.clientY - resize.startY, MIN_SIZE.height, Math.max(MIN_SIZE.height, window.innerHeight - VIEWPORT_MARGIN * 2)),
    };
    setSize((current) => (current?.width === next.width && current?.height === next.height ? current : next));
  };

  const endResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const resize = resizeState.current;
    if (resize === null || resize.pointerId !== event.pointerId) return;
    resizeState.current = null;
    event.stopPropagation();
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const cancel = () => controller.current?.abort();
  const copy = async () => { if (text) await navigator.clipboard?.writeText(text); };

  const style: CSSProperties = size === null ? position : { ...position, width: size.width, height: size.height };

  return (
    <section
      ref={popoverRef}
      className={size === null ? 'inline-assistant-popover' : 'inline-assistant-popover inline-assistant-popover--sized'}
      style={style}
      aria-label={`${title}选区`}
    >
      <header
        className="inline-assistant-popover__drag-handle"
        tabIndex={0}
        aria-label={`拖拽移动${title}弹窗`}
        onPointerDown={onHeaderPointerDown}
        onPointerMove={onHeaderPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
        onKeyDown={onHeaderKeyDown}
      >
        <strong>{title}</strong><button type="button" aria-label="关闭" onClick={() => { if (state === 'streaming') cancel(); onDismiss(); }}>关闭</button>
      </header>
      <div className="inline-assistant-popover__body">
        {modelProfileId === null ? <p role="status">请先选择可用模型后再{title}。</p> : null}
        {state === 'streaming' && text === '' ? <p role="status">正在生成{title}…</p> : null}
        {state === 'streaming' ? <button type="button" onClick={cancel}>取消</button> : null}
        {text ? <><p className="inline-assistant-popover__text">{text}</p><button type="button" onClick={() => { void copy(); }}>复制</button></> : null}
        {state === 'completed' ? <p role="status">已存入笔记</p> : null}
        {state === 'cancelled' ? <p role="status">已取消，已保留当前内容。</p> : null}
        {state === 'failed' ? <><p role="alert">{message}</p><button type="button" onClick={() => { void start(); }}>重试</button></> : null}
      </div>
      <div
        className="inline-assistant-popover__resize-handle"
        role="separator"
        aria-orientation="horizontal"
        aria-label={`调整${title}弹窗大小`}
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        onLostPointerCapture={endResize}
      />
    </section>
  );
}
