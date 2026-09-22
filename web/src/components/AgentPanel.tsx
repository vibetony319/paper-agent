import type { AgentMessage, Citation } from '../api/types';
import { useEffect, useRef } from 'react';
import type { AgentExchange } from '../workspace/types';
import { AgentStepsTimeline } from './AgentStepsTimeline';
import { MarkdownText } from './MarkdownText';

export interface AgentPanelProps {
  paperId: string | null;
  exchanges: AgentExchange[];
  onSelectCitation: (citation: Citation) => void;
  onSelectNoteReference?: (noteId: string) => void;
}

function statusLabel(status: AgentMessage['status']): string {
  return status === 'grounded' ? '模型回答' : '模型提示证据不足';
}

const KIND_LABELS: Record<string, string> = {
  paragraph: '段落',
  heading: '标题',
  table: '表格',
  text_block: '文本块',
  image: '图片',
  drawing: '图形',
};

function citationKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

export function AgentPanel({ paperId, exchanges, onSelectCitation, onSelectNoteReference }: AgentPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (exchanges.length) endRef.current?.scrollIntoView?.({ block: 'nearest' }); }, [exchanges.length]);
  return (
    <section className="agent-panel" aria-labelledby="agent-panel-title">
      <header className="agent-panel__header chat-visually-hidden">
        <div><h2 id="agent-panel-title">论文助手</h2></div>
        <p className="agent-panel__quiet-status">{paperId === null ? '请选择论文。' : '在下方输入问题。'}</p>
      </header>
      <div className="agent-panel__conversation" aria-live="polite">
        {exchanges.length === 0 ? <div className="chat-welcome"><h2>一起读懂这篇论文</h2><p>可以提问，也可以选中原文后深入讨论。</p></div> : exchanges.map(({ question, message, steps }) => (
          <article className="agent-panel__exchange" key={message.message_id}>
            <p className="agent-panel__question">{question}</p>
            <div className="agent-panel__message-meta">
              <span className="chat-feedback__name">论文助手</span>
              <span className="agent-panel__model-badge" title={statusLabel(message.status)}>{message.model?.display_name ?? ''}</span>
            </div>
            {steps.length > 0 && (
              <details className="chat-feedback__steps">
                <summary>执行过程</summary>
                <AgentStepsTimeline steps={steps} />
              </details>
            )}
            {message.paper_answer !== '' && <div className="agent-panel__answer"><MarkdownText text={message.paper_answer} citations={message.citations} onSelectCitation={onSelectCitation} /></div>}
            {message.background_explanation !== null && <section className="agent-panel__background" aria-label="背景知识说明"><h3>背景知识</h3><MarkdownText text={message.background_explanation} /></section>}
            {message.citations.length > 0 && <div className="agent-panel__citations" aria-label="论文引用">{message.citations.map((citation) => <button key={`${message.message_id}-${citation.id}`} type="button" onClick={() => onSelectCitation(citation)}>论文：第 {citation.page_number} 页 {citationKindLabel(citation.kind)}</button>)}</div>}
            {message.note_references !== undefined && message.note_references.length > 0 && <div className="agent-panel__note-references" aria-label="笔记引用">{message.note_references.map((reference) => <button key={`${message.message_id}-${reference.note_id}`} type="button" disabled={!reference.available} onClick={() => onSelectNoteReference?.(reference.note_id)}>笔记：{reference.page_number === null ? '原文位置不可用' : `第 ${reference.page_number} 页`}</button>)}</div>}
          </article>
        ))}
      </div>
      <div ref={endRef} />
    </section>
  );
}
