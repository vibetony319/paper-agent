import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, delay, http } from 'msw';
import { afterEach, expect, it, vi } from 'vitest';

import type {
  AgentMessage,
  DocumentElement,
  PaperDocument,
  PaperGraph,
  PaperSummary,
} from './api/types';
import { server } from './test/server';
import { App } from './App';

vi.mock('./components/PdfReader', () => ({
  PdfReader: ({
    activeSource,
    onSourceCleared,
  }: {
    activeSource: { kind: string; pageNumber: number } | null;
    onSourceCleared: () => void;
  }) => (
    <section aria-label="Paper reader">
      <p>{activeSource === null ? 'No active source' : `Reader page ${activeSource.pageNumber}`}</p>
      {activeSource !== null && (
        <>
          <div data-testid="source-overlay">Selected {activeSource.kind}</div>
          <button type="button" onClick={onSourceCleared}>Clear evidence highlight</button>
        </>
      )}
    </section>
  ),
}));

vi.mock('./components/GraphPanel', () => ({
  GraphPanel: ({ onSelectEvidence }: { onSelectEvidence: (elementId: string) => void }) => (
    <section>
      <h2>Paper connections</h2>
      <button type="button" onClick={() => onSelectEvidence('element-2')}>
        Select graph evidence
      </button>
    </section>
  ),
}));

const readyPaper: PaperSummary = {
  id: 'paper-a',
  original_filename: 'routing-paper.pdf',
  status: 'completed',
  stage0_status: 'completed',
  stage1_status: 'completed',
  stage2_status: 'queued',
  stage3_status: null,
  error: null,
};

const locatedElement: DocumentElement = {
  id: 'element-2',
  kind: 'paragraph',
  text: 'The router selects a sparse expert set.',
  page_number: 2,
  bbox: { x0: 0.12, y0: 0.24, x1: 0.82, y1: 0.34 },
  section_id: null,
  location_status: 'located',
  order: 2,
};

const graph: PaperGraph = {
  nodes: [{
    id: 'method',
    node_type: 'method',
    name: 'Sparse router',
    summary: 'Selects experts.',
    stage: 'core',
    evidence_element_ids: [locatedElement.id],
  }],
  edges: [],
};

function documentFor(paper: PaperSummary): PaperDocument {
  return {
    paper: {
      id: paper.id,
      original_filename: paper.original_filename,
      status: paper.status,
    },
    pages: [
      { id: `${paper.id}-page-1`, number: 1, width: 612, height: 792 },
      { id: `${paper.id}-page-2`, number: 2, width: 612, height: 792 },
    ],
    sections: [],
    elements: [locatedElement],
    notes: [],
  };
}

function useReadyWorkspaceHandlers(paper: PaperSummary = readyPaper) {
  server.use(
    http.get('/api/papers', () => HttpResponse.json([paper])),
    http.get(`/api/papers/${paper.id}/document`, () => HttpResponse.json(documentFor(paper))),
    http.get(`/api/papers/${paper.id}/graph`, () => HttpResponse.json(graph)),
    http.get(`/api/papers/${paper.id}/notes`, () => HttpResponse.json([
      {
        id: 'note-1',
        body: 'Revisit the routing result.',
        element_id: locatedElement.id,
        page_number: 2,
      },
    ])),
  );
}

afterEach(cleanup);

it('loads the paper library and opens an existing paper with readable stage states', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();

  render(<App />);

  expect(screen.getByRole('status')).toHaveTextContent('Loading paper library');
  const paperButton = await screen.findByRole('button', { name: /routing-paper\.pdf/i });
  expect(paperButton).toHaveTextContent('Geometry Ready');
  expect(paperButton).toHaveTextContent('Structure Ready');
  expect(paperButton).toHaveTextContent('Core graph Pending');
  expect(paperButton).toHaveTextContent('Deep graph Unavailable');

  await user.click(paperButton);

  expect(await screen.findByRole('heading', { name: 'routing-paper.pdf', level: 1 })).toBeVisible();
  expect(screen.getByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByLabelText('Paper graph')).toBeVisible();
  expect(screen.getByRole('complementary', { name: 'Research tools' })).toBeVisible();
});

it('opens an uploaded paper, inserts it once, and prevents a second in-flight upload', async () => {
  const user = userEvent.setup();
  let releaseUpload: (() => void) | undefined;
  const uploadedPaper: PaperSummary = {
    ...readyPaper,
    id: 'paper-uploaded',
    original_filename: 'uploaded-paper.pdf',
    stage2_status: null,
  };
  const earlierSummary: PaperSummary = {
    ...uploadedPaper,
    original_filename: 'processing-copy.pdf',
  };

  server.use(
    http.get('/api/papers', () => HttpResponse.json([earlierSummary])),
    http.post('/api/papers', async () => {
      await new Promise<void>((resolve) => {
        releaseUpload = resolve;
      });
      return HttpResponse.json(uploadedPaper);
    }),
    http.get('/api/papers/paper-uploaded/document', () => HttpResponse.json(documentFor(uploadedPaper))),
    http.get('/api/papers/paper-uploaded/graph', () => HttpResponse.json(graph)),
    http.get('/api/papers/paper-uploaded/notes', () => HttpResponse.json([])),
  );

  render(<App />);
  expect(await screen.findByRole('button', { name: /processing-copy\.pdf/i })).toBeVisible();

  const upload = screen.getByLabelText('Upload PDF');
  await user.upload(upload, new File(['%PDF-1.7'], 'uploaded-paper.pdf', {
    type: 'application/pdf',
  }));

  expect(upload).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('Uploading uploaded-paper.pdf');
  expect(releaseUpload).toBeTypeOf('function');
  releaseUpload?.();

  expect(await screen.findByRole('heading', { name: 'uploaded-paper.pdf', level: 1 })).toBeVisible();
  expect(screen.getAllByRole('button', { name: /uploaded-paper\.pdf/i })).toHaveLength(1);
  expect(screen.queryByRole('button', { name: /processing-copy\.pdf/i })).not.toBeInTheDocument();
  expect(await screen.findByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByLabelText('Paper graph')).toBeVisible();
});

it('shows an accessible empty library and workbench state', async () => {
  server.use(http.get('/api/papers', () => HttpResponse.json([])));
  render(<App />);

  expect(await screen.findByText('No papers in the library yet.')).toBeVisible();
  expect(screen.getByText('Select a paper or upload a PDF to begin.')).toBeVisible();
});

it('shows a public-safe paper library error', async () => {
  server.use(http.get('/api/papers', () => HttpResponse.json(
    { detail: 'Paper library is unavailable.' },
    { status: 503 },
  )));
  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent('Paper library is unavailable.');
  expect(screen.queryByText(/traceback|database/i)).not.toBeInTheDocument();
});

it('keeps upload failures public-safe and leaves the current library usable', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.post('/api/papers', () => HttpResponse.json(
    { internal: 'C:\\private\\paper.pdf', detail: { message: 'raw failure' } },
    { status: 500 },
  )));

  render(<App />);
  await screen.findByRole('button', { name: /routing-paper\.pdf/i });
  await user.upload(screen.getByLabelText('Upload PDF'), new File(['%PDF-'], 'bad.pdf', {
    type: 'application/pdf',
  }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Request failed.');
  expect(screen.queryByText(/private|raw failure/i)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /routing-paper\.pdf/i })).toBeEnabled();
  expect(screen.getByLabelText('Upload PDF')).toBeEnabled();
});

it('routes graph evidence, Agent citations, and note sources through one workspace selection', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  const answer: AgentMessage = {
    conversation_id: 'conversation-1',
    message_id: 'message-1',
    status: 'grounded',
    paper_answer: 'The router selects experts sparsely.',
    background_explanation: null,
    citations: [{
      id: locatedElement.id,
      kind: locatedElement.kind,
      page_number: 2,
      bbox: locatedElement.bbox!,
    }],
  };
  server.use(http.post('/api/papers/paper-a/agent/messages', () => HttpResponse.json(answer)));

  render(<App />);
  await user.click(await screen.findByRole('button', { name: /routing-paper\.pdf/i }));
  await screen.findByLabelText('Paper graph');

  await user.click(screen.getByRole('button', { name: 'Select graph evidence' }));
  expect(screen.getByText('Reader page 2')).toBeVisible();
  expect(screen.getByTestId('source-overlay')).toHaveTextContent('Selected paragraph');

  await user.click(screen.getByRole('button', { name: 'Clear evidence highlight' }));
  expect(screen.getByText('No active source')).toBeVisible();

  await user.type(screen.getByLabelText('Ask about this paper'), 'How does routing work?');
  await user.click(screen.getByRole('button', { name: 'Ask' }));
  await user.click(await screen.findByRole('button', { name: 'Page 2 paragraph' }));
  expect(screen.getByText('Reader page 2')).toBeVisible();
  expect(screen.getByTestId('source-overlay')).toBeVisible();

  await user.click(screen.getByRole('tab', { name: 'Notes' }));
  await user.click(await screen.findByRole('button', { name: 'Jump to page 2 paragraph' }));
  expect(screen.getByText('Reader page 2')).toBeVisible();
  expect(screen.getByTestId('source-overlay')).toBeVisible();
});

it('shows a safe workspace loading state and does not mount panes after a load error', async () => {
  useReadyWorkspaceHandlers();
  server.use(
    http.get('/api/papers/paper-a/document', async () => {
      await delay(80);
      return HttpResponse.json(documentFor(readyPaper));
    }),
  );

  const user = userEvent.setup();
  const { unmount } = render(<App />);
  await user.click(await screen.findByRole('button', { name: /routing-paper\.pdf/i }));
  expect(screen.getByRole('status')).toHaveTextContent('Loading paper workspace');
  expect(screen.queryByLabelText('Paper reader')).not.toBeInTheDocument();
  await screen.findByLabelText('Paper reader');
  unmount();

  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/document', () => HttpResponse.json(
    { detail: 'Paper document is not ready.' },
    { status: 409 },
  )));
  render(<App />);
  await user.click(await screen.findByRole('button', { name: /routing-paper\.pdf/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Paper document is not ready.');
  await waitFor(() => expect(screen.queryByLabelText('Paper reader')).not.toBeInTheDocument());
  expect(screen.queryByLabelText('Paper graph')).not.toBeInTheDocument();
});

it('turns a workspace network failure into a public-safe error state', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/document', () => HttpResponse.error()));

  render(<App />);
  await user.click(await screen.findByRole('button', { name: /routing-paper\.pdf/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Unable to load the paper workspace.',
  );
  expect(screen.queryByLabelText('Paper reader')).not.toBeInTheDocument();
});
