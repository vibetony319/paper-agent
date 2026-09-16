import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type { AgentMessage, ContextUsage, ModelProfile, TextAnchorDraft } from '../api/types';
import { ContextUsageBadge } from './ContextUsageBadge';
import { MarkdownText } from './MarkdownText';
import { ModelSelector } from './ModelSelector';

export type ComposerAttachment = {
  draft: TextAnchorDraft;
  token: string;
};

export interface ChatComposerProps {
  paperId: string | null;
  profiles: ModelProfile[];
  selectedModelProfileId: string | null;
  onSelectedModelProfileIdChange: (id: string) => void;
  askAgent: (
    content: string,
    modelProfileId: string,
    selection?: TextAnchorDraft,
  ) => Promise<AgentMessage | null>;
  attachment: ComposerAttachment | null;
  onAttachmentClear: (token: string) => void;
  focusRequest?: number;
  messageTarget?: HTMLDivElement | null;
  onSendStart?: () => void;
  streamingText?: string;
  streamInterrupted?: boolean;
  contextUsage?: ContextUsage | null;
}

export function ChatComposer({
  paperId,
  profiles,
  selectedModelProfileId,
  onSelectedModelProfileIdChange,
  askAgent,
  attachment,
  onAttachmentClear,
  focusRequest = 0,
  messageTarget = null,
  onSendStart,
  streamingText = '',
  streamInterrupted = false,
  contextUsage = null,
}: ChatComposerProps) {
  const [content, setContent] = useState('');
  const [pending, setPending] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [greeting, setGreeting] = useState(false);
  const [sentQuestion, setSentQuestion] = useState('');
  const feedbackRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    feedbackRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [pending, greeting, messageTarget]);
  useEffect(() => {
    if (!pending) return;
    const started = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [pending]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const inputId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const currentPaperId = useRef(paperId);
  const currentAttachment = useRef(attachment);
  const requestVersion = useRef(0);

  currentPaperId.current = paperId;
  currentAttachment.current = attachment;

  useEffect(() => {
    if (focusRequest > 0) textareaRef.current?.focus();
  }, [focusRequest]);

  useEffect(() => {
    requestVersion.current += 1;
    setContent('');
    setPending(false);
    setGreeting(false);
    setSentQuestion('');
    setErrorMessage(null);
  }, [paperId]);

  const send = async (question: string) => {
    if (!question || pending || paperId === null || selectedModelProfileId === null) return;
    const requestId = ++requestVersion.current;
    const requestPaperId = paperId;
    const sentAttachment = attachment;
    const isCurrentRequest = () => (
      requestVersion.current === requestId && currentPaperId.current === requestPaperId
    );
    setPending(true);
    setSentQuestion(question);
    setContent('');
    onSendStart?.();
    setGreeting(false);
    setErrorMessage(null);
    try {
      const response = await askAgent(question, selectedModelProfileId, sentAttachment?.draft);
      if (!isCurrentRequest()) return;
      if (response === null) {
        setContent(question);
        setErrorMessage('发送失败，请重试。');
        return;
      }
      setContent('');
      if (sentAttachment !== null && currentAttachment.current?.token === sentAttachment.token) {
        onAttachmentClear(sentAttachment.token);
      }
    } catch {
      if (!isCurrentRequest()) return;
      setContent(question);
      setErrorMessage('发送失败，请重试。');
    } finally {
      if (isCurrentRequest()) setPending(false);
    }
  };

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = content.trim();
    if (!pending && /^(你好|您好|嗨|hello|hi)[！!。.?？\s]*$/i.test(question) && attachment === null) {
      setGreeting(true); setSentQuestion(question); setContent(''); onSendStart?.(); return;
    }
    void send(question);
  };

  const unavailable = selectedModelProfileId === null;
  const interruptedText = streamInterrupted ? streamingText : '';
  const feedback = (pending || greeting || interruptedText !== '') && <div ref={feedbackRef} className="chat-feedback">
    <p className="agent-panel__question">{sentQuestion}</p>
    <div className="chat-feedback__assistant" role="status">
      <span className="chat-feedback__name">论文助手</span>
      {greeting ? <p>你好！我可以帮你概括论文、解释方法或分析选中的段落。试试问“这篇论文讲了什么”。</p> : interruptedText !== '' ? <>
        <div className="agent-panel__answer"><MarkdownText text={interruptedText} /></div>
        <p className="chat-feedback__interrupted" role="alert">回答已中断，以上为已生成的部分。</p>
        <button type="button" onClick={() => { void send(sentQuestion); }}>重新提问</button>
      </> : streamingText === '' ? <p className="chat-feedback__waiting"><span className="chat-typing" aria-hidden="true"><i /><i /><i /></span>{elapsed >= 20 ? '模型还在处理，请稍候…' : '正在生成回答…'}</p> : <div className="agent-panel__answer"><MarkdownText text={streamingText} /></div>}
    </div>
  </div>;

  return (
    <form className="chat-composer" onSubmit={submit} aria-label="论文助手输入区">
      <div className="chat-composer__controls">
        <ModelSelector
          profiles={profiles}
          value={selectedModelProfileId}
          onChange={onSelectedModelProfileIdChange}
        />
      </div>
      {attachment !== null && (
        <div className="chat-composer__attachment" aria-label="已附加选区">
          <span>选区：{attachment.draft.quote}</span>
          <button type="button" onClick={() => onAttachmentClear(attachment.token)} aria-label="移除选区附件">移除</button>
        </div>
      )}
      <label className="chat-composer__label" htmlFor={inputId}>向论文助手提问</label>
      <div className="chat-composer__input-row">
        <textarea
          ref={textareaRef}
          id={inputId}
          rows={2}
          placeholder="围绕这篇论文，问点什么…"
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={pending || paperId === null || unavailable}
        />
        <button type="submit" disabled={pending || !content.trim() || paperId === null || unavailable}>
          {pending ? '发送中…' : '发送'}
        </button>
      </div>
      {unavailable && <p className="chat-composer__guidance">请先在当前模型中选择可用模型，再发送问题。</p>}
      <div className="chat-composer__hint-row">
        <p className="chat-composer__hint">Enter 发送，Shift+Enter 换行</p>
        {contextUsage !== null && <ContextUsageBadge usage={contextUsage} />}
      </div>
      {messageTarget ? createPortal(feedback, messageTarget) : feedback}
      {errorMessage !== null && <p className="chat-composer__error" role="alert">{errorMessage}</p>}
    </form>
  );
}
