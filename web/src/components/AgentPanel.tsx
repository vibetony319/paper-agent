import type { AgentMessage, Citation, ConversationSummary, Note } from '../api/types';
import { useEffect, useRef, useState } from 'react';
import type { AgentExchange } from '../workspace/types';
import { AgentStepsTimeline } from './AgentStepsTimeline';
import { MarkdownText } from './MarkdownText';

export interface AgentPanelProps {
  paperId: string | null;
  exchanges: AgentExchange[];
  onSelectCitation: (citation: Citation) => void;
  onSelectNoteReference?: (noteId: string) => void;
  conversations?: ConversationSummary[];
  conversationId?: string | null;
  historyLoading?: boolean;
  conversationBusy?: boolean;
  onSelectConversation?: (conversationId: string | null) => void;
  onAddToNotes?: (body: string) => Promise<Note | null>;
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

export function AgentPanel({ paperId, exchanges, onSelectCitation, onSelectNoteReference, conversations = [], conversationId = null, historyLoading = false, conversationBusy = false, onSelectConversation, onAddToNotes }: AgentPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savedIds, setSavedIds] = useState<Set<string>>(() => new Set());
  const [saveError, setSaveError] = useState<{ id: string; message: string } | null>(null);
  const saveRequestVersion = useRef(0);
  useEffect(() => { if (exchanges.length) endRef.current?.scrollIntoView?.({ block: 'nearest' }); }, [exchanges.length]);
  useEffect(() => { saveRequestVersion.current += 1; setSavingId(null); setSavedIds(new Set()); setSaveError(null); }, [paperId, conversationId]);
  const jumpToExchange = (messageId: string) => {
    document.getElementById(`exchange-${messageId}`)?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };
  const addToNotes = async (messageId: string, body: string) => {
    if (onAddToNotes === undefined || savingId !== null) return;
    const version = saveRequestVersion.current;
    setSavingId(messageId); setSaveError(null);
    try {
      const note = await onAddToNotes(body);
      if (version !== saveRequestVersion.current) return;
      if (note === null) setSaveError({ id: messageId, message: '保存笔记失败，请重试。' });
      else setSavedIds((current) => new Set(current).add(messageId));
    } catch {
      if (version === saveRequestVersion.current) setSaveError({ id: messageId, message: '保存笔记失败，请重试。' });
    } finally { if (version === saveRequestVersion.current) setSavingId(null); }
  };
  return (
    <section className="agent-panel" aria-labelledby="agent-panel-title">
      <header className="agent-panel__header chat-visually-hidden">
        <div><h2 id="agent-panel-title">论文助手</h2></div>
        <p className="agent-panel__quiet-status">{paperId === null ? '请选择论文。' : '在下方输入问题。'}</p>
      </header>
      {onSelectConversation !== undefined && <div className="agent-panel__history">
        <label htmlFor="conversation-history">对话记录</label>
        <select id="conversation-history" value={conversationId ?? ''} disabled={historyLoading || conversationBusy || savingId !== null} onChange={(event) => onSelectConversation(event.target.value || null)}>
          <option value="">新对话</option>
          {conversations.map((conversation, index) => <option key={conversation.id} value={conversation.id}>{`对话 ${conversations.length - index} · ${conversation.first_question.slice(0, 36)}`}</option>)}
        </select>
        {conversationId !== null && <button type="button" disabled={historyLoading || conversationBusy} onClick={() => onSelectConversation(null)}>新建对话</button>}
      </div>}
      {exchanges.length > 1 && <details className="agent-panel__index"><summary>对话索引 · {exchanges.length} 轮</summary><nav aria-label="对话索引"><ol>{exchanges.map(({ question, message }, index) => <li key={message.message_id}><button type="button" onClick={() => jumpToExchange(message.message_id)} title={question}>第 {index + 1} 轮 · {question.slice(0, 42)}</button></li>)}</ol></nav></details>}
      <div className="agent-panel__conversation" aria-live="polite">
        {historyLoading ? <p role="status">正在加载对话记录…</p> : exchanges.length === 0 ? <div className="chat-welcome"><h2>一起读懂这篇论文</h2><p>可以提问，也可以选中原文后深入讨论。</p></div> : exchanges.map(({ question, message, steps }) => (
          <article className="agent-panel__exchange" key={message.message_id} id={`exchange-${message.message_id}`}>
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
            {onAddToNotes !== undefined && message.paper_answer.trim() !== '' && <div className="agent-panel__actions"><button type="button" disabled={savingId !== null || savedIds.has(message.message_id)} onClick={() => { const body = message.paper_answer.replace(/\[\[([^\]]+)\]\]/g, (_marker, id: string) => { const citation = message.citations.find((item) => item.id === id.trim()); return citation === undefined ? '（引用位置不可用）' : `（论文第 ${citation.page_number} 页）`; }); void addToNotes(message.message_id, body); }}>{savedIds.has(message.message_id) ? '已添加到笔记' : savingId === message.message_id ? '保存中…' : '添加到笔记'}</button>{saveError?.id === message.message_id && savingId === null && <span role="alert">{saveError.message}</span>}</div>}
          </article>
        ))}
      </div>
      <div ref={endRef} />
    </section>
  );
}
