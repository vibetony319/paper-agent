import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('./PdfReader', () => ({
  PdfReader: ({ selectionActions }: { selectionActions?: { ask?: (draft: { quote: string; page_number: number; rects: [] }) => void } }) => (
    <button type="button" onClick={() => selectionActions?.ask?.({ quote: '选区内容', page_number: 2, rects: [] })}>问助手</button>
  ),
}));

vi.mock('./ResizableSplit', () => ({
  ResizableSplit: ({ paper, tools }: { paper: React.ReactNode; tools: React.ReactNode }) => <div>{paper}{tools}</div>,
}));

import type { AgentMessage, ModelProfile, Note, PaperSummary, TextAnchor } from '../api/types';
import { WorkspaceShell } from './WorkspaceShell';

const profile: ModelProfile = {
  id: 'qwen', display_name: '本地 Qwen', base_url: 'http://localhost/v1', model_name: 'qwen3', enabled: true, is_default: true, revision: 1,
  has_api_key: false, api_key_mask: null, capabilities: { basic_chat: true, structured_output: true, tool_calling: false, checked_at: null }, read_only: false,
};

const paper: PaperSummary = {
  id: 'paper-a', original_filename: 'paper.pdf', status: 'completed', stage0_status: 'completed', stage1_status: 'completed', stage2_status: 'completed', stage3_status: null, error: null,
};

function workspaceFixture() {
  return {
    activePaperId: 'paper-a', loadRevision: 1,
    document: { paper: { id: 'paper-a', original_filename: 'paper.pdf', status: 'completed' as const }, pages: [], sections: [], elements: [], notes: [] },
    graph: { nodes: [], edges: [] }, notes: [] as Note[], anchors: [] as TextAnchor[], highlights: [], selection: null, activeSource: null, messages: [] as AgentMessage[], errorMessage: null, notesErrorMessage: null,
    clearActiveSource: vi.fn(), setSelection: vi.fn(), clearSelection: vi.fn(), createHighlight: vi.fn(), deleteHighlight: vi.fn(), runSelectionAssist: vi.fn(), saveNote: vi.fn(),
    selectCitation: vi.fn(), selectGraphEvidenceElement: vi.fn(), selectAnchorSource: vi.fn(), updateNote: vi.fn(), deleteNote: vi.fn(),
    askAgent: vi.fn(), buildCoreGraph: vi.fn(), buildDeepGraph: vi.fn(),
  };
}

afterEach(cleanup);

it('attaches the current selection to the always-mounted composer without sending and focuses it', async () => {
  const user = userEvent.setup();
  const workspace = workspaceFixture();
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  await user.click(screen.getByRole('button', { name: '问助手' }));
  expect(workspace.askAgent).not.toHaveBeenCalled();
  expect(screen.getByLabelText('已附加选区')).toHaveTextContent('选区内容');
  expect(screen.getByLabelText('向论文助手提问')).toHaveFocus();
});

it('keeps the composer mounted while switching all three right-side tabs', async () => {
  const user = userEvent.setup();
  const workspace = workspaceFixture();
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  for (const tab of ['论文助手', '知识图谱', '笔记']) {
    await user.click(screen.getByRole('tab', { name: tab }));
    expect(screen.getByLabelText('向论文助手提问')).toBeInTheDocument();
  }
  expect(screen.getAllByText('当前模型')).toHaveLength(1);
});

it('shows immutable response model badges and locates persisted note references separately from paper citations', async () => {
  const user = userEvent.setup();
  const workspace = workspaceFixture();
  workspace.messages = [{
    conversation_id: 'conversation-a', message_id: 'message-a', status: 'grounded', paper_answer: '回答', background_explanation: null, citations: [],
    model: { profile_id: 'qwen', display_name: '本地 Qwen', base_url: 'http://localhost/v1', model_name: 'qwen3', revision: 1 },
    note_references: [{ note_id: 'note-a', note_type: 'manual', page_number: 2, available: true }],
  }];
  workspace.notes = [{ id: 'note-a', body: '笔记', element_id: null, page_number: 2, anchor_ids: ['anchor-a'] }];
  workspace.anchors = [{ id: 'anchor-a', quote: '持久原文', page_number: 2, element_id: null, rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }] }];
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  expect(screen.getAllByText('本地 Qwen')[0]).toBeVisible();
  await user.click(screen.getByRole('button', { name: '笔记：第 2 页' }));
  expect(workspace.selectAnchorSource).toHaveBeenCalledWith(expect.objectContaining({ id: 'anchor-a', kind: 'text_anchor' }));
});
