import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

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

export function InlineAssistantPopover({
  action, draft, toolbarRect, modelProfileId, runSelectionAssist, onDismiss,
}: InlineAssistantPopoverProps) {
  const [state, setState] = useState<PopoverState>('idle');
  const [text, setText] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const requestId = useRef<string | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const selectionBottom = toolbarRect.bottom || toolbarRect.top + toolbarRect.height;
  const [position, setPosition] = useState({ left: toolbarRect.left, top: selectionBottom + 8 });
  const title = action === 'explain' ? '解释' : '翻译';

  useLayoutEffect(() => {
    const bounds = popoverRef.current?.getBoundingClientRect();
    const width = bounds?.width ?? 320;
    setPosition({
      left: Math.min(Math.max(12, toolbarRect.left), Math.max(12, window.innerWidth - width - 12)),
      top: Math.max(12, (toolbarRect.bottom || toolbarRect.top + toolbarRect.height) + 8),
    });
  }, [toolbarRect]);

  const start = useCallback(async () => {
    if (modelProfileId === null || controller.current !== null) return;
    requestId.current ??= crypto.randomUUID();
    const nextController = new AbortController();
    controller.current = nextController;
    setState('streaming');
    setText('');
    setMessage(null);
    const result = await runSelectionAssist(
      action, draft, modelProfileId, requestId.current, nextController.signal,
      (delta) => setText((current) => current + delta),
    );
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
    return () => { window.clearTimeout(timer); controller.current?.abort(); };
  }, [action, draft, modelProfileId, start]);

  const cancel = () => controller.current?.abort();
  const copy = async () => { if (text) await navigator.clipboard?.writeText(text); };

  return (
    <section ref={popoverRef} className="inline-assistant-popover" style={position} aria-label={`${title}选区`}>
      <header><strong>{title}</strong><button type="button" aria-label="关闭" onClick={() => { if (state === 'streaming') cancel(); onDismiss(); }}>关闭</button></header>
      {modelProfileId === null ? <p role="status">请先选择可用模型后再{title}。</p> : null}
      {state === 'streaming' ? <button type="button" onClick={cancel}>取消</button> : null}
      {text ? <><p className="inline-assistant-popover__text">{text}</p><button type="button" onClick={() => { void copy(); }}>复制</button></> : null}
      {state === 'completed' ? <p role="status">已存入笔记</p> : null}
      {state === 'cancelled' ? <p role="status">已取消，已保留当前内容。</p> : null}
      {state === 'failed' ? <><p role="alert">{message}</p><button type="button" onClick={() => { void start(); }}>重试</button></> : null}
    </section>
  );
}
