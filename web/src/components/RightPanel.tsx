import { useEffect, useId, useState } from 'react';

import { AgentPanel, type AgentPanelProps } from './AgentPanel';
import { ChatComposer, type ChatComposerProps } from './ChatComposer';
import { GraphPanel, type GraphPanelProps } from './GraphPanel';
import { NotesPanel, type NotesPanelProps } from './NotesPanel';

export interface RightPanelProps {
  paperId: string | null;
  agent: Omit<AgentPanelProps, 'paperId'>;
  graph: Omit<GraphPanelProps, 'paperId'>;
  notes: Omit<NotesPanelProps, 'paperId'>;
  composer: Omit<ChatComposerProps, 'paperId'>;
}

type PanelTab = 'agent' | 'graph' | 'notes';

export function RightPanel({ paperId, agent, graph, notes, composer }: RightPanelProps) {
  const [selectedTab, setSelectedTab] = useState<PanelTab>('agent');
  const tabId = useId();
  useEffect(() => { setSelectedTab('agent'); }, [paperId]);
  const tabs: Array<[PanelTab, string]> = [['agent', '论文助手'], ['graph', '知识图谱'], ['notes', '笔记']];

  return (
    <aside className="right-panel" aria-label="研究工具">
      <div className="right-panel__tabs" role="tablist" aria-label="研究工具">
        {tabs.map(([tab, label]) => {
          const tabControlId = `${tabId}-${tab}-tab`;
          const panelId = `${tabId}-${tab}-panel`;
          return <button key={tab} id={tabControlId} type="button" role="tab" aria-selected={selectedTab === tab} aria-controls={panelId} onClick={() => setSelectedTab(tab)}>{label}</button>;
        })}
      </div>
      <div className="right-panel__content">
        <div id={`${tabId}-agent-panel`} role="tabpanel" aria-labelledby={`${tabId}-agent-tab`} hidden={selectedTab !== 'agent'}><AgentPanel paperId={paperId} {...agent} /></div>
        <div id={`${tabId}-graph-panel`} role="tabpanel" aria-labelledby={`${tabId}-graph-tab`} hidden={selectedTab !== 'graph'}><GraphPanel paperId={paperId ?? ''} {...graph} /></div>
        <div id={`${tabId}-notes-panel`} role="tabpanel" aria-labelledby={`${tabId}-notes-tab`} hidden={selectedTab !== 'notes'}><NotesPanel paperId={paperId} {...notes} /></div>
      </div>
      <ChatComposer paperId={paperId} {...composer} />
    </aside>
  );
}
