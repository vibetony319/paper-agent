import { useEffect, useId, useRef, useState } from 'react';

import type { AgentMessage, AgentMode, ModelProfile, TextAnchorDraft } from '../api/types';
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
    mode: AgentMode,
    modelProfileId: string,
    selection?: TextAnchorDraft,
  ) => Promise<AgentMessage | null>;
  attachment: ComposerAttachment | null;
  onAttachmentClear: (token: string) => void;
  focusRequest?: number;
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
}: ChatComposerProps) {
  const [content, setContent] = useState('');
  const [mode, setMode] = useState<AgentMode>('paper_only');
  const [pending, setPending] = useState(false);
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
    setMode('paper_only');
    setPending(false);
    setErrorMessage(null);
  }, [paperId]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const question = content.trim();
    if (!question || pending || paperId === null || selectedModelProfileId === null) return;
    const requestId = ++requestVersion.current;
    const requestPaperId = paperId;
    const sentAttachment = attachment;
    const isCurrentRequest = () => (
      requestVersion.current === requestId && currentPaperId.current === requestPaperId
    );
    setPending(true);
    setErrorMessage(null);
    try {
      const response = await askAgent(question, mode, selectedModelProfileId, sentAttachment?.draft);
      if (!isCurrentRequest()) return;
      if (response === null) {
        setErrorMessage('发送失败，请重试。');
        return;
      }
      setContent('');
      if (sentAttachment !== null && currentAttachment.current?.token === sentAttachment.token) {
        onAttachmentClear(sentAttachment.token);
      }
    } catch {
      if (!isCurrentRequest()) return;
      setErrorMessage('发送失败，请重试。');
    } finally {
      if (isCurrentRequest()) setPending(false);
    }
  };

  const unavailable = selectedModelProfileId === null;

  return (
    <form className="chat-composer" onSubmit={submit} aria-label="论文助手输入区">
      <div className="chat-composer__controls">
        <ModelSelector
          profiles={profiles}
          value={selectedModelProfileId}
          onChange={onSelectedModelProfileIdChange}
        />
        <fieldset className="chat-composer__mode" disabled={pending || paperId === null}>
          <legend>回答范围</legend>
          <label>
            <input type="radio" name="agent-mode" checked={mode === 'paper_only'} onChange={() => setMode('paper_only')} />
            仅基于论文
          </label>
          <label>
            <input type="radio" name="agent-mode" checked={mode === 'external_knowledge'} onChange={() => setMode('external_knowledge')} />
            允许背景知识
          </label>
        </fieldset>
      </div>
      {attachment !== null && (
        <div className="chat-composer__attachment" aria-label="已附加选区">
          <span>选区：{attachment.draft.quote}</span>
          <button type="button" onClick={() => onAttachmentClear(attachment.token)} aria-label="移除选区附件">移除</button>
        </div>
      )}
      <label htmlFor={inputId}>向论文助手提问</label>
      <div className="chat-composer__input-row">
        <textarea
          ref={textareaRef}
          id={inputId}
          rows={3}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={pending || paperId === null || unavailable}
        />
        <button type="submit" disabled={pending || !content.trim() || paperId === null || unavailable}>
          {pending ? '发送中…' : '发送'}
        </button>
      </div>
      {unavailable && <p className="chat-composer__guidance">请先在当前模型中选择可用模型，再发送问题。</p>}
      {errorMessage !== null && <p className="chat-composer__error" role="alert">{errorMessage}</p>}
    </form>
  );
}
