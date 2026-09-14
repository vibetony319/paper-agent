import { useEffect, useId, useRef, useState } from 'react';

import { AgentPanel, type AgentPanelProps } from './AgentPanel';
import { ChatComposer, type ChatComposerProps } from './ChatComposer';
import { NotesPanel, type NotesPanelProps } from './NotesPanel';

export interface RightPanelProps {
  paperId: string | null;
  agent: Omit<AgentPanelProps, 'paperId'>;
  notes: Omit<NotesPanelProps, 'paperId'>;
  composer: Omit<ChatComposerProps, 'paperId'>;
}

type PanelTab = 'agent' | 'notes';

export function RightPanel({ paperId, agent, notes, composer }: RightPanelProps) {
  const [selectedTab, setSelectedTab] = useState<PanelTab>('agent');
  const [messageTarget, setMessageTarget] = useState<HTMLDivElement | null>(null);
  const tabId = useId();
  const tabRefs = useRef<Record<PanelTab, HTMLButtonElement | null>>({ agent: null, notes: null });
  useEffect(() => { setSelectedTab('agent'); }, [paperId]);
  const tabs: Array<[PanelTab, string]> = [['agent', '论文助手'], ['notes', '笔记']];

  return (
    <aside className="right-panel" aria-label="研究工具">
      <div className="right-panel__tabs" role="tablist" aria-label="研究工具">
        {tabs.map(([tab, label]) => {
          const tabControlId = `${tabId}-${tab}-tab`;
          const panelId = `${tabId}-${tab}-panel`;
          return <button
            key={tab}
            ref={(element) => { tabRefs.current[tab] = element; }}
            id={tabControlId}
            type="button"
            role="tab"
            tabIndex={selectedTab === tab ? 0 : -1}
            aria-selected={selectedTab === tab}
            aria-controls={panelId}
            onClick={() => setSelectedTab(tab)}
            onKeyDown={(event) => {
              const index = tabs.findIndex(([value]) => value === tab);
              const nextIndex = event.key === 'ArrowRight' ? (index + 1) % tabs.length
                : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                  : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
              if (nextIndex === null) return;
              event.preventDefault();
              const nextTab = tabs[nextIndex][0];
              setSelectedTab(nextTab);
              tabRefs.current[nextTab]?.focus();
            }}
          >{label}</button>;
        })}
      </div>
      <div className="right-panel__content">
        <div id={`${tabId}-agent-panel`} role="tabpanel" aria-labelledby={`${tabId}-agent-tab`} hidden={selectedTab !== 'agent'}><AgentPanel paperId={paperId} {...agent} /><div className="chat-live-messages" ref={setMessageTarget} /></div>
        <div id={`${tabId}-notes-panel`} role="tabpanel" aria-labelledby={`${tabId}-notes-tab`} hidden={selectedTab !== 'notes'}><NotesPanel paperId={paperId} {...notes} /></div>
      </div>
      <ChatComposer paperId={paperId} {...composer} messageTarget={messageTarget} onSendStart={() => setSelectedTab('agent')} />
    </aside>
  );
}
