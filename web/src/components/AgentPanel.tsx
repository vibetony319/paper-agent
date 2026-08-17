import { useEffect, useMemo, useRef, useState } from 'react';

import type { AgentMessage, AgentMode, Citation } from '../api/types';

export interface AgentPanelProps {
  paperId: string | null;
  messages: AgentMessage[];
  askAgent: (content: string, mode: AgentMode) => Promise<AgentMessage | null>;
  onSelectCitation: (citation: Citation) => void;
}

type AgentExchange = {
  question: string | null;
  message: AgentMessage;
};

function statusLabel(status: AgentMessage['status']): string {
  return status === 'grounded'
    ? 'Grounded in this paper'
    : 'Insufficient paper evidence';
}

export function AgentPanel({
  paperId,
  messages,
  askAgent,
  onSelectCitation,
}: AgentPanelProps) {
  const [draft, setDraft] = useState('');
  const [mode, setMode] = useState<AgentMode>('paper_only');
  const [pending, setPending] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [localExchanges, setLocalExchanges] = useState<AgentExchange[]>([]);
  const [uiPaperId, setUiPaperId] = useState<string | null>(paperId);
  const currentPaperId = useRef(paperId);
  const requestId = useRef(0);

  currentPaperId.current = paperId;

  useEffect(() => {
    requestId.current += 1;
    setUiPaperId(paperId);
    setDraft('');
    setMode('paper_only');
    setPending(false);
    setErrorMessage(null);
    setLocalExchanges([]);
  }, [paperId]);

  useEffect(() => () => {
    requestId.current += 1;
  }, []);

  const isCurrentPaper = uiPaperId === paperId;
  const exchanges = useMemo(() => {
    if (!isCurrentPaper) {
      return [];
    }

    const localByMessageId = new Map(
      localExchanges.map((exchange) => [exchange.message.message_id, exchange]),
    );
    const authoritative = messages.map((message) => (
      localByMessageId.get(message.message_id) ?? { question: null, message }
    ));
    const authoritativeIds = new Set(messages.map((message) => message.message_id));

    return [
      ...authoritative,
      ...localExchanges.filter((exchange) => !authoritativeIds.has(exchange.message.message_id)),
    ];
  }, [isCurrentPaper, localExchanges, messages]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || pending || paperId === null) {
      return;
    }

    const activeRequestId = ++requestId.current;
    const requestPaperId = paperId;
    setPending(true);
    setErrorMessage(null);

    try {
      const response = await askAgent(content, mode);
      if (
        requestId.current !== activeRequestId
        || currentPaperId.current !== requestPaperId
      ) {
        return;
      }
      if (response === null) {
        setErrorMessage('Unable to receive an Agent response.');
        return;
      }

      setLocalExchanges((current) => {
        if (current.some((exchange) => exchange.message.message_id === response.message_id)) {
          return current;
        }
        return [...current, { question: content, message: response }];
      });
      setDraft('');
    } catch {
      if (requestId.current === activeRequestId && currentPaperId.current === requestPaperId) {
        setErrorMessage('Unable to receive an Agent response.');
      }
    } finally {
      if (requestId.current === activeRequestId && currentPaperId.current === requestPaperId) {
        setPending(false);
      }
    }
  };

  return (
    <section className="agent-panel" aria-labelledby="agent-panel-title">
      <header className="agent-panel__header">
        <div>
          <p className="agent-panel__eyebrow">Evidence assistant</p>
          <h2 id="agent-panel-title">Agent</h2>
        </div>
        <p className="agent-panel__quiet-status" aria-live="polite">
          {paperId === null ? 'Choose a paper to begin.' : 'Ready for a question.'}
        </p>
      </header>

      <form className="agent-panel__form" onSubmit={submit}>
        <label htmlFor="agent-question">Ask about this paper</label>
        <textarea
          id="agent-question"
          value={isCurrentPaper ? draft : ''}
          onChange={(event) => setDraft(event.target.value)}
          disabled={pending || paperId === null}
          rows={4}
        />
        <fieldset className="agent-panel__mode" disabled={pending || paperId === null}>
          <legend>Answer scope</legend>
          <label>
            <input
              type="radio"
              name="agent-mode"
              value="paper_only"
              checked={mode === 'paper_only'}
              onChange={() => setMode('paper_only')}
            />
            Paper only
          </label>
          <label>
            <input
              type="radio"
              name="agent-mode"
              value="external_knowledge"
              checked={mode === 'external_knowledge'}
              onChange={() => setMode('external_knowledge')}
            />
            Background knowledge
          </label>
        </fieldset>
        <button type="submit" disabled={pending || draft.trim() === '' || paperId === null}>
          {pending ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {errorMessage !== null && <p className="agent-panel__error" role="alert">{errorMessage}</p>}

      <div className="agent-panel__conversation" aria-live="polite">
        {exchanges.length === 0 ? (
          <p className="agent-panel__empty">Responses will appear here after the server finishes.</p>
        ) : exchanges.map(({ question, message }) => (
          <article className="agent-panel__exchange" key={message.message_id}>
            {question !== null && <p className="agent-panel__question">{question}</p>}
            <p className={`agent-panel__status agent-panel__status--${message.status}`}>
              {statusLabel(message.status)}
            </p>
            {message.paper_answer !== '' && <p className="agent-panel__answer">{message.paper_answer}</p>}
            {message.background_explanation !== null && (
              <section className="agent-panel__background" aria-label="Background knowledge explanation">
                <h3>Background knowledge</h3>
                <p>{message.background_explanation}</p>
              </section>
            )}
            {message.citations.length > 0 && (
              <div className="agent-panel__citations" aria-label="Answer citations">
                {message.citations.map((citation) => (
                  <button
                    key={`${message.message_id}-${citation.id}`}
                    type="button"
                    onClick={() => onSelectCitation(citation)}
                  >
                    Page {citation.page_number} {citation.kind}
                  </button>
                ))}
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
