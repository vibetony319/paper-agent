import type { AgentMessage, Citation } from '../api/types';
import type { AgentExchange } from '../workspace/types';

export interface AgentPanelProps {
  paperId: string | null;
  exchanges: AgentExchange[];
  onSelectCitation: (citation: Citation) => void;
  onSelectNoteReference?: (noteId: string) => void;
}

function statusLabel(status: AgentMessage['status']): string {
  return status === 'grounded' ? '已基于论文证据回答' : '论文证据不足';
}

export function AgentPanel({ paperId, exchanges, onSelectCitation, onSelectNoteReference }: AgentPanelProps) {
  return (
    <section className="agent-panel" aria-labelledby="agent-panel-title">
      <header className="agent-panel__header">
        <div><p className="agent-panel__eyebrow">论文助手</p><h2 id="agent-panel-title">论文助手</h2></div>
        <p className="agent-panel__quiet-status">{paperId === null ? '请选择论文。' : '在下方输入问题。'}</p>
      </header>
      <div className="agent-panel__conversation" aria-live="polite">
        {exchanges.length === 0 ? <p className="agent-panel__empty">回答会显示在这里。</p> : exchanges.map(({ question, message }) => (
          <article className="agent-panel__exchange" key={message.message_id}>
            <p className="agent-panel__question">{question}</p>
            <div className="agent-panel__message-meta">
              <p className={`agent-panel__status agent-panel__status--${message.status}`}>{statusLabel(message.status)}</p>
              <span className="agent-panel__model-badge">{message.model?.display_name ?? '旧版本模型记录不可用'}</span>
            </div>
            {message.paper_answer !== '' && <p className="agent-panel__answer">{message.paper_answer}</p>}
            {message.background_explanation !== null && <section className="agent-panel__background" aria-label="背景知识说明"><h3>背景知识</h3><p>{message.background_explanation}</p></section>}
            {message.citations.length > 0 && <div className="agent-panel__citations" aria-label="论文引用">{message.citations.map((citation) => <button key={`${message.message_id}-${citation.id}`} type="button" onClick={() => onSelectCitation(citation)}>论文：第 {citation.page_number} 页 {citation.kind}</button>)}</div>}
            {message.note_references !== undefined && message.note_references.length > 0 && <div className="agent-panel__note-references" aria-label="笔记引用">{message.note_references.map((reference) => <button key={`${message.message_id}-${reference.note_id}`} type="button" disabled={!reference.available} onClick={() => onSelectNoteReference?.(reference.note_id)}>笔记：{reference.page_number === null ? '原文位置不可用' : `第 ${reference.page_number} 页`}</button>)}</div>}
          </article>
        ))}
      </div>
    </section>
  );
}
