import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('./PdfReader', () => ({
  PdfReader: ({ selectionActions, selectionErrorMessage }: { selectionActions?: { ask?: (draft: { quote: string; page_number: number; rects: [] }) => void }; selectionErrorMessage?: string | null }) => (
    <>
      <button type="button" onClick={() => selectionActions?.ask?.({ quote: '选区内容', page_number: 2, rects: [] })}>问助手</button>
      {selectionErrorMessage === undefined || selectionErrorMessage === null ? null : <p role="alert">{selectionErrorMessage}</p>}
    </>
  ),
}));

vi.mock('./ResizableSplit', () => ({
  ResizableSplit: ({ paper, tools }: { paper: React.ReactNode; tools: React.ReactNode }) => <div data-testid="workspace-split">{paper}{tools}</div>,
}));

import type { AgentMessage, ModelProfile, Note, PaperSummary, TextAnchor } from '../api/types';
import { WorkspaceShell } from './WorkspaceShell';

const profile: ModelProfile = {
  id: 'qwen', display_name: '本地 Qwen', base_url: 'http://localhost/v1', model_name: 'qwen3', enabled: true, is_default: true, revision: 1,
  has_api_key: false, api_key_mask: null, context_length: null, max_output_tokens: null,
  capabilities: { basic_chat: true, structured_output: true, tool_calling: false, checked_at: null }, read_only: false,
};

const paper: PaperSummary = {
  id: 'paper-a', original_filename: 'paper.pdf', status: 'completed', stage0_status: 'completed', stage1_status: 'completed', error: null,
};

const paperB: PaperSummary = { ...paper, id: 'paper-b', original_filename: 'paper-b.pdf' };

function workspaceFixture() {
  return {
    activePaperId: 'paper-a', loadRevision: 1,
    document: { paper: { id: 'paper-a', original_filename: 'paper.pdf', status: 'completed' as const }, pages: [], sections: [], elements: [], notes: [] },
    notes: [] as Note[], anchors: [] as TextAnchor[], highlights: [], selection: null, activeSource: null, messages: [] as AgentMessage[], exchanges: [] as Array<{ question: string; message: AgentMessage }>, errorMessage: null as string | null, notesErrorMessage: null as string | null,
    clearActiveSource: vi.fn(), setSelection: vi.fn(), clearSelection: vi.fn(), createHighlight: vi.fn(), deleteHighlight: vi.fn(), runSelectionAssist: vi.fn(), saveNote: vi.fn(),
    selectCitation: vi.fn(), selectElementSource: vi.fn(), selectAnchorSource: vi.fn(), updateNote: vi.fn(), deleteNote: vi.fn(),
    askAgent: vi.fn(),
  };
}

afterEach(cleanup);

it('exposes stable topbar regions for the compact mobile layout', () => {
  const workspace = workspaceFixture();
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  expect(screen.getByRole('button', { name: '返回论文库' })).toHaveClass('workspace-topbar__back');
  expect(screen.getByRole('heading', { name: 'paper.pdf', level: 1 }).parentElement).toHaveClass('workspace-topbar__title');
  expect(screen.getByRole('button', { name: '模型设置' })).toHaveClass('workspace-topbar__model');
});

it('attaches the current selection to the always-mounted composer without sending and focuses it', async () => {
  const user = userEvent.setup();
  const workspace = workspaceFixture();
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  expect(screen.getByTestId('workspace-content').querySelector('.workspace-shell__errors')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: '问助手' }));
  expect(workspace.askAgent).not.toHaveBeenCalled();
  expect(screen.getByLabelText('已附加选区')).toHaveTextContent('选区内容');
  expect(screen.getByLabelText('向论文助手提问')).toHaveFocus();
});

it('does not carry a paper A selection attachment into a paper B Agent request', async () => {
  const user = userEvent.setup();
  const workspaceA = workspaceFixture();
  const workspaceB = { ...workspaceFixture(), activePaperId: 'paper-b', document: { ...workspaceA.document, paper: { ...workspaceA.document.paper, id: 'paper-b', original_filename: 'paper-b.pdf' } }, askAgent: vi.fn().mockResolvedValue(null) };
  const { rerender } = render(<WorkspaceShell paper={paper} workspace={workspaceA as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  await user.click(screen.getByRole('button', { name: '问助手' }));
  expect(screen.getByLabelText('已附加选区')).toHaveTextContent('选区内容');
  rerender(<WorkspaceShell paper={paperB} workspace={workspaceB as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  expect(screen.queryByLabelText('已附加选区')).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('向论文助手提问'), '论文 B 的问题');
  await user.click(screen.getByRole('button', { name: '发送' }));
  await waitFor(() => expect(workspaceB.askAgent).toHaveBeenCalledWith('论文 B 的问题', 'qwen', undefined));
});

it('keeps the composer mounted while switching all three right-side tabs', async () => {
  const user = userEvent.setup();
  const workspace = workspaceFixture();
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  for (const tab of ['论文助手', '笔记']) {
    await user.click(screen.getByRole('tab', { name: tab }));
    expect(screen.getByLabelText('向论文助手提问')).toBeInTheDocument();
  }
  expect(screen.getAllByText('当前模型')).toHaveLength(1);
});

it('keeps concurrent workspace and annotation errors inside the constrained split content', () => {
  const workspace = workspaceFixture();
  workspace.errorMessage = '文档加载失败。';
  workspace.notesErrorMessage = '批注加载失败。';
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  const content = screen.getByTestId('workspace-content');
  expect(content).toHaveClass('workspace-shell__content');
  expect(content.querySelector('.workspace-shell__errors')).toBeTruthy();
  expect(content).toContainElement(screen.getByText('文档加载失败。'));
  expect(content).toContainElement(screen.getByText('批注加载失败。'));
  expect(content).toContainElement(screen.getByTestId('workspace-split'));
});

it('renders one workspace failure only in the workspace error shell', () => {
  const workspace = workspaceFixture();
  workspace.errorMessage = '文档加载失败。';
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  expect(screen.getAllByRole('alert')).toHaveLength(1);
  expect(screen.getByRole('alert')).toHaveTextContent('文档加载失败。');
});

it('shows submitted questions, immutable model badges, and routes paper and note sources separately', async () => {
  const user = userEvent.setup();
  const workspace = workspaceFixture();
  workspace.messages = [{
    conversation_id: 'conversation-a', message_id: 'message-a', status: 'grounded', paper_answer: '回答', background_explanation: null, citations: [{ id: 'element-a', kind: 'paragraph', page_number: 2, bbox: { x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 } }],
    model: { profile_id: 'qwen', display_name: '本地 Qwen', base_url: 'http://localhost/v1', model_name: 'qwen3', revision: 1 },
    note_references: [{ note_id: 'note-a', note_type: 'manual', page_number: 2, available: true }],
  }];
  workspace.exchanges = [{ question: '这个回答来自哪里？', message: workspace.messages[0] }];
  workspace.notes = [{ id: 'note-a', body: '笔记', element_id: null, page_number: 2, anchor_ids: ['anchor-a'] }];
  workspace.anchors = [{ id: 'anchor-a', quote: '持久原文', page_number: 2, element_id: null, rects: [{ order: 0, x0: 0.1, y0: 0.2, x1: 0.6, y1: 0.3 }] }];
  render(<WorkspaceShell paper={paper} workspace={workspace as never} onRetryPaperLoading={vi.fn()} onReturnToLibrary={vi.fn()} onOpenModelSettings={vi.fn()} onDeleteRequested={vi.fn()} modelProfiles={[profile]} selectedModelProfileId="qwen" onSelectedModelProfileIdChange={vi.fn()} />);

  expect(screen.getAllByText('本地 Qwen')[0]).toBeVisible();
  expect(screen.getByText('这个回答来自哪里？')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '论文：第 2 页 paragraph' }));
  expect(workspace.selectCitation).toHaveBeenCalledWith(expect.objectContaining({ id: 'element-a' }));
  await user.click(screen.getByRole('button', { name: '笔记：第 2 页' }));
  expect(workspace.selectAnchorSource).toHaveBeenCalledWith(expect.objectContaining({ id: 'anchor-a', kind: 'text_anchor' }));
});
