import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { RightPanel } from './RightPanel';

vi.mock('./AgentPanel', () => ({ AgentPanel: () => <div>助手内容</div> }));
vi.mock('./GraphPanel', () => ({ GraphPanel: () => <div>图谱内容</div> }));
vi.mock('./NotesPanel', () => ({ NotesPanel: () => <div>笔记内容</div> }));
vi.mock('./ChatComposer', () => ({ ChatComposer: () => <div>聊天输入</div> }));

afterEach(cleanup);

function renderPanel() {
  render(
    <RightPanel
      paperId="paper-a"
      agent={{ exchanges: [], onSelectCitation: vi.fn(), onSelectNoteReference: vi.fn() }}
      graph={{ graph: { nodes: [], edges: [] }, documentElements: [], onSelectEvidence: vi.fn(), selectedModelProfileId: null, stageModel: null, buildCoreGraph: vi.fn(), buildDeepGraph: vi.fn() }}
      notes={{ activeSource: null, notes: [], anchors: [], documentElements: [], saveNote: vi.fn(), onSelectSource: vi.fn(), onSelectAnchor: vi.fn(), updateNote: vi.fn(), deleteNote: vi.fn() }}
      composer={{ profiles: [], selectedModelProfileId: null, onSelectedModelProfileIdChange: vi.fn(), askAgent: vi.fn(), attachment: null, onAttachmentClear: vi.fn(), focusRequest: 0 }}
    />,
  );
}

it('uses a roving Chinese tab pattern with arrow, home, and end keys', () => {
  renderPanel();
  const agent = screen.getByRole('tab', { name: '论文助手' });
  const graph = screen.getByRole('tab', { name: '知识图谱' });
  const notes = screen.getByRole('tab', { name: '笔记' });

  expect(agent).toHaveAttribute('tabindex', '0');
  expect(graph).toHaveAttribute('tabindex', '-1');
  agent.focus();
  fireEvent.keyDown(agent, { key: 'ArrowRight' });
  expect(graph).toHaveFocus();
  expect(graph).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(graph, { key: 'End' });
  expect(notes).toHaveFocus();
  fireEvent.keyDown(notes, { key: 'Home' });
  expect(agent).toHaveFocus();
  fireEvent.keyDown(agent, { key: 'ArrowLeft' });
  expect(notes).toHaveFocus();
});
