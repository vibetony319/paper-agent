import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, delay, http } from 'msw';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type {
  AgentMessage,
  DocumentElement,
  ModelProfile,
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
  GraphPanel: ({
    graph,
    onSelectEvidence,
    selectedModelProfileId,
    buildCoreGraph,
  }: {
    graph: PaperGraph;
    onSelectEvidence: (elementId: string) => void;
    selectedModelProfileId: string | null;
    buildCoreGraph: (modelProfileId: string) => Promise<PaperGraph | null>;
  }) => (
    <section aria-label="论文图谱">
      <h2>论文关系</h2>
      <p>Graph node count: {graph.nodes.length}</p>
      <button type="button" disabled={selectedModelProfileId === null} onClick={() => selectedModelProfileId !== null && void buildCoreGraph(selectedModelProfileId)}>
        构建核心图谱
      </button>
      <button type="button" onClick={() => onSelectEvidence('element-2')}>
        选择图谱证据
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

const modelProfileFixture = (overrides: Partial<ModelProfile> = {}): ModelProfile => ({
  id: 'qwen',
  display_name: '本地 Qwen',
  base_url: 'http://127.0.0.1:8001/v1',
  model_name: 'qwen3',
  enabled: true,
  is_default: true,
  revision: 3,
  has_api_key: false,
  api_key_mask: null,
  capabilities: {
    basic_chat: true,
    structured_output: true,
    tool_calling: true,
    checked_at: '2026-08-21T09:30:00Z',
  },
  read_only: false,
  ...overrides,
});

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
    stage: 'stage2',
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
    http.get(`/api/papers/${paper.id}/annotations`, () => HttpResponse.json({
      highlights: [],
      notes: [],
    })),
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

beforeEach(() => {
  window.localStorage.clear();
  server.use(http.get('/api/model-profiles', () => HttpResponse.json([])));
});

it('loads the paper library and opens an existing paper with readable stage states', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();

  render(<App />);

  expect(screen.getByRole('status')).toHaveTextContent('正在加载论文库');
  const paperButton = await screen.findByRole('button', { name: '打开 routing-paper.pdf' });
  expect(paperButton).toHaveTextContent('页面定位 已完成');
  expect(paperButton).toHaveTextContent('结构解析 已完成');
  expect(paperButton).toHaveTextContent('核心图谱 等待中');
  expect(paperButton).toHaveTextContent('深度图谱 未开始');

  await user.click(paperButton);

  expect(await screen.findByRole('heading', { name: 'routing-paper.pdf', level: 1 })).toBeVisible();
  expect(screen.getByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByRole('tab', { name: '知识图谱' })).toBeVisible();
  expect(screen.getByRole('complementary', { name: '研究工具' })).toBeVisible();
});

it('uses a focused library view and returns there after permanent deletion', async () => {
  const user = userEvent.setup();
  const remainingPaper: PaperSummary = {
    ...readyPaper,
    id: 'paper-b',
    original_filename: 'remaining-paper.pdf',
  };
  let deleted = false;
  useReadyWorkspaceHandlers();
  server.use(
    http.get('/api/papers', () => HttpResponse.json(
      deleted ? [remainingPaper] : [readyPaper, remainingPaper],
    )),
    http.delete('/api/papers/paper-a', () => {
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );

  render(<App />);

  expect(await screen.findByRole('heading', { name: '论文库' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '打开 routing-paper.pdf' }));
  await user.click(screen.getByRole('button', { name: '论文操作' }));
  await user.click(screen.getByRole('menuitem', { name: '删除论文' }));
  expect(screen.getByText('PDF、解析结果、知识图谱、高亮、笔记和对话将被永久删除。')).toBeVisible();

  await user.click(screen.getByRole('button', { name: '确认永久删除' }));

  expect(await screen.findByRole('heading', { name: '论文库' })).toHaveFocus();
  expect(screen.queryByRole('button', { name: '打开 routing-paper.pdf' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '打开 remaining-paper.pdf' })).toBeVisible();
});

it('moves focus through the paper actions menu and restores it after Escape', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();

  render(<App />);

  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));
  const actionsButton = screen.getByRole('button', { name: '论文操作' });
  actionsButton.focus();

  await user.keyboard('{Enter}');
  const deleteItem = screen.getByRole('menuitem', { name: '删除论文' });
  expect(deleteItem).toHaveFocus();

  await user.keyboard('{ArrowDown}');
  expect(deleteItem).toHaveFocus();
  await user.keyboard('{ArrowUp}');
  expect(deleteItem).toHaveFocus();

  await user.keyboard('{Escape}');
  expect(screen.queryByRole('menuitem', { name: '删除论文' })).not.toBeInTheDocument();
  expect(actionsButton).toHaveFocus();
});

it('returns to the focused library without losing its papers', async () => {
  const user = userEvent.setup();
  const secondPaper: PaperSummary = {
    ...readyPaper,
    id: 'paper-b',
    original_filename: 'second-paper.pdf',
  };
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers', () => HttpResponse.json([readyPaper, secondPaper])));

  render(<App />);

  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));
  expect(await screen.findByRole('heading', { name: 'routing-paper.pdf', level: 1 })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '返回论文库' }));

  expect(await screen.findByRole('heading', { name: '论文库' })).toBeVisible();
  expect(screen.getByRole('button', { name: '打开 routing-paper.pdf' })).toBeVisible();
  expect(screen.getByRole('button', { name: '打开 second-paper.pdf' })).toBeVisible();
});

it('opens permanent deletion from a library paper and keeps the reader after a busy conflict', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.delete('/api/papers/paper-a', () => HttpResponse.json(
    { code: 'PAPER_BUSY', detail: '内部错误：Paper is busy at 10.0.0.7' },
    { status: 409 },
  )));

  render(<App />);

  await user.click(await screen.findByRole('button', { name: '删除 routing-paper.pdf' }));
  expect(screen.getByRole('dialog', { name: '删除论文' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '取消' }));

  await user.click(screen.getByRole('button', { name: '打开 routing-paper.pdf' }));
  await screen.findByLabelText('Paper reader');
  await user.click(screen.getByRole('button', { name: '论文操作' }));
  await user.click(screen.getByRole('menuitem', { name: '删除论文' }));
  await user.click(screen.getByRole('button', { name: '确认永久删除' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('论文正在处理中，请稍后重试。');
  expect(screen.getByRole('heading', { name: 'routing-paper.pdf', level: 1 })).toBeVisible();
  expect(screen.getByLabelText('Paper reader')).toBeVisible();
});

it('refreshes the active header and library stages after a successful core graph build', async () => {
  const user = userEvent.setup();
  const unbuiltPaper: PaperSummary = {
    ...readyPaper,
    stage2_status: null,
  };
  const builtPaper: PaperSummary = {
    ...unbuiltPaper,
    stage2_status: 'completed',
  };
  const builtGraph: PaperGraph = {
    nodes: [{
      ...graph.nodes[0],
      id: 'built-node',
    }],
    edges: [],
  };
  let listedPaper = unbuiltPaper;
  server.use(
    http.get('/api/papers', () => HttpResponse.json([listedPaper])),
    http.get('/api/papers/paper-a/document', () => HttpResponse.json(documentFor(unbuiltPaper))),
    http.get('/api/papers/paper-a/graph', () => HttpResponse.json({ nodes: [], edges: [] })),
    http.get('/api/papers/paper-a/annotations', () => HttpResponse.json({ highlights: [], notes: [] })),
    http.get('/api/papers/paper-a/notes', () => HttpResponse.json([])),
    http.post('/api/papers/paper-a/graph/core', () => {
      listedPaper = builtPaper;
      return HttpResponse.json(builtGraph);
    }),
    http.get('/api/papers/paper-a', () => HttpResponse.json(builtPaper)),
    http.get('/api/model-profiles', () => HttpResponse.json([modelProfileFixture()])),
  );

  render(<App />);
  const paperButton = await screen.findByRole('button', { name: '打开 routing-paper.pdf' });
  await user.click(paperButton);
  await user.click(screen.getByRole('tab', { name: '知识图谱' }));
  expect(await screen.findByText('Graph node count: 0')).toBeVisible();
  expect(screen.getByText(/核心图谱 未开始/)).toBeVisible();
  expect(paperButton).toHaveTextContent('核心图谱 未开始');

  await user.click(screen.getByRole('button', { name: '构建核心图谱' }));

  expect(await screen.findByText('Graph node count: 1')).toBeVisible();
  await waitFor(() => {
    expect(screen.getByText(/核心图谱 已完成/)).toBeVisible();
  });
  await user.click(screen.getByRole('button', { name: '返回论文库' }));
  expect(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }))
    .toHaveTextContent('核心图谱 已完成');
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
    http.get('/api/papers/paper-uploaded/annotations', () => HttpResponse.json({ highlights: [], notes: [] })),
    http.get('/api/papers/paper-uploaded/notes', () => HttpResponse.json([])),
  );

  render(<App />);
  expect(await screen.findByRole('button', { name: '打开 processing-copy.pdf' })).toBeVisible();

  const upload = screen.getByLabelText('上传 PDF');
  await user.upload(upload, new File(['%PDF-1.7'], 'uploaded-paper.pdf', {
    type: 'application/pdf',
  }));

  expect(upload).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('正在上传 uploaded-paper.pdf');
  expect(releaseUpload).toBeTypeOf('function');
  releaseUpload?.();

  expect(await screen.findByRole('heading', { name: 'uploaded-paper.pdf', level: 1 })).toBeVisible();
  expect(await screen.findByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByRole('tab', { name: '知识图谱' })).toBeVisible();

  await user.click(screen.getByRole('button', { name: '返回论文库' }));
  expect(await screen.findByRole('button', { name: '打开 uploaded-paper.pdf' })).toBeVisible();
  expect(screen.queryByRole('button', { name: '打开 processing-copy.pdf' })).not.toBeInTheDocument();
});

it('shows an accessible empty library and workbench state', async () => {
  server.use(http.get('/api/papers', () => HttpResponse.json([])));
  render(<App />);

  expect(await screen.findByText('论文库中还没有论文。')).toBeVisible();
  expect(screen.getByRole('heading', { name: '论文库' })).toBeVisible();
});

it('shows a public-safe paper library error', async () => {
  server.use(http.get('/api/papers', () => HttpResponse.json(
    { detail: 'Paper library is unavailable.' },
    { status: 503 },
  )));
  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent('请求失败，请稍后重试。');
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
  await screen.findByRole('button', { name: '打开 routing-paper.pdf' });
  await user.upload(screen.getByLabelText('上传 PDF'), new File(['%PDF-'], 'bad.pdf', {
    type: 'application/pdf',
  }));

  expect(await screen.findByRole('alert')).toHaveTextContent('请求失败，请稍后重试。');
  expect(screen.queryByText(/private|raw failure/i)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '打开 routing-paper.pdf' })).toBeEnabled();
  expect(screen.getByLabelText('上传 PDF')).toBeEnabled();
});

it('routes graph evidence, Agent citations, and note sources through one workspace selection', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/model-profiles', () => HttpResponse.json([modelProfileFixture()])));
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
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));
  await user.click(screen.getByRole('tab', { name: '知识图谱' }));
  await screen.findByLabelText('论文图谱');

  await user.click(screen.getByRole('button', { name: '选择图谱证据' }));
  expect(screen.getByText('Reader page 2')).toBeVisible();
  expect(screen.getByTestId('source-overlay')).toHaveTextContent('Selected paragraph');

  await user.click(screen.getByRole('button', { name: 'Clear evidence highlight' }));
  expect(screen.getByText('No active source')).toBeVisible();

  await user.click(screen.getByRole('tab', { name: '论文助手' }));
  await user.type(screen.getByLabelText('向论文助手提问'), 'How does routing work?');
  await user.click(screen.getByRole('button', { name: '发送' }));
  await user.click(await screen.findByRole('button', { name: '论文：第 2 页 paragraph' }));
  expect(screen.getByText('Reader page 2')).toBeVisible();
  expect(screen.getByTestId('source-overlay')).toBeVisible();

  await user.click(screen.getByRole('tab', { name: '笔记' }));
  await user.click(await screen.findByRole('button', { name: '定位到第 2 页 paragraph' }));
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
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));
  expect(screen.getByRole('status')).toHaveTextContent('正在加载论文阅读工作区');
  expect(screen.queryByLabelText('Paper reader')).not.toBeInTheDocument();
  await screen.findByLabelText('Paper reader');
  unmount();

  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/document', () => HttpResponse.json(
    { detail: 'Paper document is not ready.' },
    { status: 409 },
  )));
  render(<App />);
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('论文阅读工作区暂时无法加载。');
  await waitFor(() => expect(screen.queryByLabelText('Paper reader')).not.toBeInTheDocument());
  expect(screen.queryByRole('tab', { name: '知识图谱' })).not.toBeInTheDocument();
});

it('retries a transient document conflict from the workspace action', async () => {
  const user = userEvent.setup();
  let documentRequests = 0;
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/document', () => {
    documentRequests += 1;
    return documentRequests === 1
      ? HttpResponse.json({ detail: 'Paper document is not ready.' }, { status: 409 })
      : HttpResponse.json(documentFor(readyPaper));
  }));

  render(<App />);
  const paperButton = await screen.findByRole('button', { name: '打开 routing-paper.pdf' });
  await user.click(paperButton);
  expect(await screen.findByRole('alert')).toHaveTextContent('论文阅读工作区暂时无法加载。');

  await user.click(screen.getByRole('button', { name: '重试加载论文' }));

  expect(await screen.findByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByRole('tab', { name: '知识图谱' })).toBeVisible();
  expect(documentRequests).toBe(2);
});

it('retries a transient graph failure from the blocking workspace action', async () => {
  const user = userEvent.setup();
  let graphRequests = 0;
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/graph', () => {
    graphRequests += 1;
    return graphRequests === 1
      ? HttpResponse.json({ detail: 'Paper graph is temporarily unavailable.' }, { status: 503 })
      : HttpResponse.json(graph);
  }));

  render(<App />);
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '论文阅读工作区暂时无法加载。',
  );

  await user.click(screen.getByRole('button', { name: '重试加载论文' }));

  expect(await screen.findByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByRole('tab', { name: '知识图谱' })).toBeVisible();
  expect(graphRequests).toBe(2);
});

it('keeps the research panes ready and reports a notes-only load failure', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/notes', () => HttpResponse.json(
    { detail: 'Paper notes are temporarily unavailable.' },
    { status: 503 },
  )));

  render(<App />);
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));

  expect(await screen.findByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByRole('tab', { name: '知识图谱' })).toBeVisible();
  expect(screen.getByRole('complementary', { name: '研究工具' })).toBeVisible();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '论文笔记暂时无法加载。',
  );
});

it('opens the reader, graph, and tools while the notes request is unresolved', async () => {
  const user = userEvent.setup();
  let notesRequestStarted = false;
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/notes', async ({ request }) => {
    notesRequestStarted = true;
    await new Promise<void>((resolve) => {
      if (request.signal.aborted) {
        resolve();
      } else {
        request.signal.addEventListener('abort', () => resolve(), { once: true });
      }
    });
    return HttpResponse.json([]);
  }));

  render(<App />);
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));

  expect(await screen.findByLabelText('Paper reader')).toBeVisible();
  expect(screen.getByRole('tab', { name: '知识图谱' })).toBeVisible();
  expect(screen.getByRole('complementary', { name: '研究工具' })).toBeVisible();
  expect(notesRequestStarted).toBe(true);
});

it('turns a workspace network failure into a public-safe error state', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/papers/paper-a/document', () => HttpResponse.error()));

  render(<App />);
  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    '论文阅读工作区暂时无法加载。',
  );
  expect(screen.queryByLabelText('Paper reader')).not.toBeInTheDocument();
});

it('selects the default model near the chat box and keeps the choice per paper', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/model-profiles', () => HttpResponse.json([
    modelProfileFixture({ id: 'qwen', display_name: '本地 Qwen', is_default: true }),
    modelProfileFixture({ id: 'deepseek', display_name: 'DeepSeek 远程', is_default: false }),
  ])));

  render(<App />);

  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));
  await screen.findByLabelText('Paper reader');

  const selector = await screen.findByLabelText('当前模型');
  await waitFor(() => expect(selector).toHaveValue('qwen'));

  await user.selectOptions(selector, 'deepseek');

  expect(selector).toHaveValue('deepseek');
  expect(window.localStorage.getItem('paper-agent:selected-model:paper-a')).toBe('deepseek');
});

it('opens the chinese model settings dialog from the top bar and returns focus', async () => {
  const user = userEvent.setup();
  useReadyWorkspaceHandlers();
  server.use(http.get('/api/model-profiles', () => HttpResponse.json([
    modelProfileFixture(),
  ])));

  render(<App />);

  await user.click(await screen.findByRole('button', { name: '打开 routing-paper.pdf' }));

  await user.click(screen.getByRole('button', { name: '模型设置' }));

  const dialog = await screen.findByRole('dialog', { name: '模型设置' });
  expect(dialog).toBeVisible();
  expect(within(dialog).getByRole('listitem', { name: '本地 Qwen' })).toBeVisible();

  await user.click(within(dialog).getByRole('button', { name: '关闭' }));

  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: '模型设置' })).not.toBeInTheDocument();
  });
  expect(screen.getByRole('button', { name: '模型设置' })).toHaveFocus();
});
