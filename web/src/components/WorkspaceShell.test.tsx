import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import type { AgentMessage, Citation, DocumentElement, Note } from '../api/types';
import type { SourceTarget } from '../workspace/types';
import { AgentPanel } from './AgentPanel';
import { NotesPanel } from './NotesPanel';
import { RightPanel } from './RightPanel';

const citation: Citation = {
  id: 'element-3',
  kind: 'paragraph',
  page_number: 3,
  bbox: { x0: 0.1, y0: 0.2, x1: 0.8, y1: 0.3 },
};

const groundedResponse: AgentMessage = {
  conversation_id: 'conversation-1',
  message_id: 'message-1',
  status: 'grounded',
  paper_answer: 'The method routes tokens through a learned gate.',
  background_explanation: 'A router is a learned dispatch mechanism.',
  citations: [citation],
};

const sourceTarget: SourceTarget = {
  id: 'element-3',
  kind: 'paragraph',
  pageNumber: 3,
  bbox: citation.bbox,
};

const locatedElement: DocumentElement = {
  id: 'element-3',
  kind: 'paragraph',
  text: 'Located source.',
  page_number: 3,
  bbox: citation.bbox,
  section_id: null,
  location_status: 'located',
  order: 3,
};

afterEach(cleanup);

it('sends a paper-only question, keeps the server status visible, and jumps on citation click', async () => {
  const user = userEvent.setup();
  const askAgent = vi.fn().mockResolvedValue(groundedResponse);
  const onSelectCitation = vi.fn();

  render(
    <AgentPanel
      paperId="paper-a"
      messages={[]}
      askAgent={askAgent}
      onSelectCitation={onSelectCitation}
    />,
  );

  await user.type(screen.getByLabelText('Ask about this paper'), 'What is the method?');
  await user.click(screen.getByRole('button', { name: 'Ask' }));

  await waitFor(() => expect(askAgent).toHaveBeenCalledWith('What is the method?', 'paper_only'));
  expect(await screen.findByText('Grounded in this paper')).toBeVisible();
  expect(screen.getByText('What is the method?')).toBeVisible();
  expect(screen.getByText(groundedResponse.paper_answer)).toBeVisible();
  expect(screen.getByText(groundedResponse.background_explanation!)).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Page 3 paragraph' }));
  expect(onSelectCitation).toHaveBeenCalledWith(citation);
});

it('does not advance the visible conversation after an empty or failed Agent response', async () => {
  const user = userEvent.setup();
  const askAgent = vi.fn().mockResolvedValue(null);

  render(
    <AgentPanel
      paperId="paper-a"
      messages={[]}
      askAgent={askAgent}
      onSelectCitation={vi.fn()}
    />,
  );

  await user.type(screen.getByLabelText('Ask about this paper'), '   ');
  expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled();

  await user.clear(screen.getByLabelText('Ask about this paper'));
  await user.type(screen.getByLabelText('Ask about this paper'), 'Will this persist?');
  await user.click(screen.getByRole('button', { name: 'Ask' }));

  await waitFor(() => expect(askAgent).toHaveBeenCalledOnce());
  expect(askAgent).toHaveBeenCalledWith('Will this persist?', 'paper_only');
  expect(await screen.findByRole('alert'))
    .toHaveTextContent('Unable to receive an Agent response.');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Ask about this paper')).toHaveValue('Will this persist?');
  expect(screen.queryByText('Grounded in this paper')).not.toBeInTheDocument();
});

it('shows the insufficient-evidence status without inventing citations or explanation', () => {
  const insufficient: AgentMessage = {
    ...groundedResponse,
    message_id: 'message-insufficient',
    status: 'insufficient_evidence',
    paper_answer: '',
    background_explanation: null,
    citations: [],
  };

  render(
    <AgentPanel
      paperId="paper-a"
      messages={[insufficient]}
      askAgent={vi.fn()}
      onSelectCitation={vi.fn()}
    />,
  );

  expect(screen.getByText('Insufficient paper evidence')).toBeVisible();
  expect(screen.queryByRole('button', { name: /page/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/background explanation/i)).not.toBeInTheDocument();
});

it('ignores an Agent response that resolves after the active paper changes', async () => {
  const user = userEvent.setup();
  let resolveResponse: ((message: AgentMessage | null) => void) | undefined;
  const askAgent = vi.fn(() => new Promise<AgentMessage | null>((resolve) => {
    resolveResponse = resolve;
  }));

  const { rerender } = render(
    <AgentPanel
      paperId="paper-a"
      messages={[]}
      askAgent={askAgent}
      onSelectCitation={vi.fn()}
    />,
  );

  await user.type(screen.getByLabelText('Ask about this paper'), 'Old paper question');
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  rerender(
    <AgentPanel
      paperId="paper-b"
      messages={[]}
      askAgent={askAgent}
      onSelectCitation={vi.fn()}
    />,
  );
  resolveResponse?.(groundedResponse);

  await waitFor(() => expect(screen.queryByText(groundedResponse.paper_answer)).not.toBeInTheDocument());
  expect(screen.queryByText('Old paper question')).not.toBeInTheDocument();
});

it('ignores a null Agent result that resolves after the active paper changes', async () => {
  const user = userEvent.setup();
  let resolveResponse: ((message: AgentMessage | null) => void) | undefined;
  const askAgent = vi.fn(() => new Promise<AgentMessage | null>((resolve) => {
    resolveResponse = resolve;
  }));

  const { rerender } = render(
    <AgentPanel
      paperId="paper-a"
      messages={[]}
      askAgent={askAgent}
      onSelectCitation={vi.fn()}
    />,
  );

  await user.type(screen.getByLabelText('Ask about this paper'), 'Old paper question');
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  expect(askAgent).toHaveBeenCalledWith('Old paper question', 'paper_only');
  rerender(
    <AgentPanel
      paperId="paper-b"
      messages={[]}
      askAgent={askAgent}
      onSelectCitation={vi.fn()}
    />,
  );
  await act(async () => {
    resolveResponse?.(null);
    await Promise.resolve();
  });

  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Ask about this paper')).toHaveValue('');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});

it('creates a note attached to the active source target and de-duplicates its authoritative update', async () => {
  const user = userEvent.setup();
  const savedNote: Note = {
    id: 'note-1', body: 'Check this assumption.', element_id: sourceTarget.id, page_number: 3,
  };
  const saveNote = vi.fn().mockResolvedValue(savedNote);

  const { rerender } = render(
    <NotesPanel
      paperId="paper-a"
      activeSource={sourceTarget}
      notes={[]}
      documentElements={[locatedElement]}
      saveNote={saveNote}
      onSelectSource={vi.fn()}
    />,
  );

  expect(screen.getByText('Attached to page 3 paragraph.')).toBeVisible();
  await user.type(screen.getByLabelText('New note'), savedNote.body);
  await user.click(screen.getByRole('button', { name: 'Save note' }));

  await waitFor(() => expect(saveNote)
    .toHaveBeenCalledWith(savedNote.body, sourceTarget.id, sourceTarget.pageNumber));
  expect(screen.getAllByText(savedNote.body)).toHaveLength(1);

  rerender(
    <NotesPanel
      paperId="paper-a"
      activeSource={sourceTarget}
      notes={[savedNote]}
      documentElements={[locatedElement]}
      saveNote={saveNote}
      onSelectSource={vi.fn()}
    />,
  );
  expect(screen.getAllByText(savedNote.body)).toHaveLength(1);
});

it('retains an unbound note draft and shows a safe alert when save returns null', async () => {
  const user = userEvent.setup();
  const saveNote = vi.fn().mockResolvedValue(null);

  render(
    <NotesPanel
      paperId="paper-a"
      activeSource={null}
      notes={[]}
      documentElements={[]}
      saveNote={saveNote}
      onSelectSource={vi.fn()}
    />,
  );

  expect(screen.getByText('No source selected. This note will be unbound.')).toBeVisible();
  await user.type(screen.getByLabelText('New note'), 'A free-standing observation.');
  await user.click(screen.getByRole('button', { name: 'Save note' }));
  await waitFor(() => expect(saveNote)
    .toHaveBeenCalledWith('A free-standing observation.', undefined, undefined));
  expect(await screen.findByRole('alert')).toHaveTextContent('Unable to save this note.');
  expect(screen.getByLabelText('New note')).toHaveValue('A free-standing observation.');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});

it('only enables jumps for notes with a valid element location', async () => {
  const user = userEvent.setup();
  const onSelectSource = vi.fn();
  const malformedElement: DocumentElement = {
    ...locatedElement,
    id: 'malformed',
    bbox: { x0: 0.5, y0: 0.2, x1: 0.5, y1: 0.3 },
  };

  render(
    <NotesPanel
      paperId="paper-a"
      activeSource={null}
      notes={[
        { id: 'located-note', body: 'Located note.', element_id: 'element-3', page_number: 3 },
        { id: 'bad-note', body: 'Unlocatable note.', element_id: 'malformed', page_number: 3 },
        { id: 'free-note', body: 'Free note.', element_id: null, page_number: null },
      ]}
      documentElements={[locatedElement, malformedElement]}
      saveNote={vi.fn().mockResolvedValue(null)}
      onSelectSource={onSelectSource}
    />,
  );

  await user.click(screen.getByRole('button', { name: 'Jump to page 3 paragraph' }));
  expect(onSelectSource).toHaveBeenCalledWith('element-3');
  expect(screen.getByRole('button', { name: 'Source location unavailable' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Jump to source' })).not.toBeInTheDocument();
});

it('ignores a null note result that resolves after the active paper changes', async () => {
  const user = userEvent.setup();
  let resolveNote: ((note: Note | null) => void) | undefined;
  const saveNote = vi.fn(() => new Promise<Note | null>((resolve) => {
    resolveNote = resolve;
  }));

  const { rerender } = render(
    <NotesPanel
      paperId="paper-a"
      activeSource={sourceTarget}
      notes={[]}
      documentElements={[locatedElement]}
      saveNote={saveNote}
      onSelectSource={vi.fn()}
    />,
  );

  await user.type(screen.getByLabelText('New note'), 'Old paper note.');
  await user.click(screen.getByRole('button', { name: 'Save note' }));
  expect(saveNote).toHaveBeenCalledWith('Old paper note.', sourceTarget.id, sourceTarget.pageNumber);
  rerender(
    <NotesPanel
      paperId="paper-b"
      activeSource={null}
      notes={[]}
      documentElements={[]}
      saveNote={saveNote}
      onSelectSource={vi.fn()}
    />,
  );
  await act(async () => {
    resolveNote?.(null);
    await Promise.resolve();
  });

  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByLabelText('New note')).toHaveValue('');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
});

it('keeps both panel drafts mounted while switching accessible tabs', async () => {
  const user = userEvent.setup();

  render(
    <RightPanel
      paperId="paper-a"
      agent={{ messages: [], askAgent: vi.fn(), onSelectCitation: vi.fn() }}
      notes={{
        activeSource: null,
        notes: [],
        documentElements: [],
        saveNote: vi.fn(),
        onSelectSource: vi.fn(),
      }}
    />,
  );

  expect(screen.getByRole('tab', { name: 'Agent' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: 'Notes' })).toHaveAttribute('aria-selected', 'false');
  await user.type(screen.getByLabelText('Ask about this paper'), 'Keep this draft.');
  await user.click(screen.getByRole('tab', { name: 'Notes' }));
  await user.type(screen.getByLabelText('New note'), 'Keep this note.');
  await user.click(screen.getByRole('tab', { name: 'Agent' }));

  expect(screen.getByLabelText('Ask about this paper')).toHaveValue('Keep this draft.');
  await user.click(screen.getByRole('tab', { name: 'Notes' }));
  expect(screen.getByLabelText('New note')).toHaveValue('Keep this note.');
});
