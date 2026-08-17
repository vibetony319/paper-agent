import { useEffect, useId, useState } from 'react';

import { AgentPanel } from './AgentPanel';
import type { AgentPanelProps } from './AgentPanel';
import { NotesPanel } from './NotesPanel';
import type { NotesPanelProps } from './NotesPanel';

export interface RightPanelProps {
  paperId: string | null;
  agent: Omit<AgentPanelProps, 'paperId'>;
  notes: Omit<NotesPanelProps, 'paperId'>;
}

type PanelTab = 'agent' | 'notes';

export function RightPanel({ paperId, agent, notes }: RightPanelProps) {
  const [selectedTab, setSelectedTab] = useState<PanelTab>('agent');
  const tabId = useId();

  useEffect(() => {
    setSelectedTab('agent');
  }, [paperId]);

  const agentTabId = `${tabId}-agent-tab`;
  const notesTabId = `${tabId}-notes-tab`;
  const agentPanelId = `${tabId}-agent-panel`;
  const notesPanelId = `${tabId}-notes-panel`;

  return (
    <aside className="right-panel" aria-label="Research tools">
      <div className="right-panel__tabs" role="tablist" aria-label="Research tools">
        <button
          id={agentTabId}
          type="button"
          role="tab"
          aria-selected={selectedTab === 'agent'}
          aria-controls={agentPanelId}
          onClick={() => setSelectedTab('agent')}
        >
          Agent
        </button>
        <button
          id={notesTabId}
          type="button"
          role="tab"
          aria-selected={selectedTab === 'notes'}
          aria-controls={notesPanelId}
          onClick={() => setSelectedTab('notes')}
        >
          Notes
        </button>
      </div>
      <div
        id={agentPanelId}
        role="tabpanel"
        aria-labelledby={agentTabId}
        hidden={selectedTab !== 'agent'}
      >
        <AgentPanel paperId={paperId} {...agent} />
      </div>
      <div
        id={notesPanelId}
        role="tabpanel"
        aria-labelledby={notesTabId}
        hidden={selectedTab !== 'notes'}
      >
        <NotesPanel paperId={paperId} {...notes} />
      </div>
    </aside>
  );
}
