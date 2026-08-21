# 中文论文阅读工作区前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前固定三栏英文工作台升级为中文双栏论文阅读器，支持连续 PDF 文字选择、高亮、解释/翻译浮层、锚定笔记、多模型聊天和安全删除。

**Architecture:** App 在论文库与阅读工作区之间切换；阅读区左侧由一个 PDFDocumentProxy 驱动虚拟化页面、TextLayer 和批注层，右侧工作台承载论文助手、知识图谱和笔记，底部聊天框始终保留当前模型选择。所有持久状态通过后端 API，浏览器只保存布局宽度和每篇论文上次选中的模型 ID。

**Tech Stack:** React、TypeScript、Vite、PDF.js、React Flow、Vitest、Testing Library、MSW、CSS。

**Spec:** `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md`

## Global Constraints

- 必须先完成模型档案、批注笔记和安全删除三份后端计划。
- 实施本计划前必须完整读取并使用 `$design-taste-frontend`：`C:/Users/Admin/.codex/skills/taste-skill/SKILL.md`。
- 设计参数固定为 `DESIGN_VARIANCE=4`、`MOTION_INTENSITY=3`、`VISUAL_DENSITY=7`。
- UI 面向中文用户；除 PDF、vLLM、API、JSON、Agent 等必要技术名词外，用户文案使用简体中文。
- 不增加论文总结、论文评价、相关推荐、分享等未确认功能。
- 桌面默认 PDF 62%、工具区 38%，可拖动；小屏改为论文/工具分页切换。
- 聊天框当前模型同时用于解释、翻译、Agent 和知识图谱构建。
- 切换模型继续当前会话，不清空历史或自动新建会话。
- 首版只支持单页内可复制文字选区；扫描版和跨页选择显示中文提示。
- 解释/翻译不进入主聊天记录，成功结果自动出现在笔记中。
- 未经 Citation Guard 校验的主聊天论文回答不得被前端当作正式答案流式展示。
- Node 版本满足 `^22.14.0 || >=24.0.0`。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## File Map

**Create**

- `web/src/api/sse.ts`：安全解析 `text/event-stream`。
- `web/src/modelProfiles/useModelProfiles.ts`：模型列表、默认回退和每篇论文选择。
- `web/src/components/ModelSelector.tsx`
- `web/src/components/ModelSettingsDialog.tsx`
- `web/src/components/ConfirmDeleteDialog.tsx`
- `web/src/components/ResizableSplit.tsx`
- `web/src/components/PdfPageView.tsx`
- `web/src/components/AnnotationOverlay.tsx`
- `web/src/components/SelectionToolbar.tsx`
- `web/src/components/InlineAssistantPopover.tsx`
- `web/src/components/ChatComposer.tsx`
- `web/src/components/pdfSelection.ts`
- `web/src/components/pdfSelection.test.ts`
- 对应新组件的 `*.test.tsx`

**Modify**

- `web/package-lock.json`
- `web/src/api/types.ts`
- `web/src/api/client.ts`
- `web/src/api/client.test.ts`
- `web/src/pdfjs.ts`
- `web/src/pdfjs.test.ts`
- `web/src/App.tsx`
- `web/src/App.test.tsx`
- `web/src/components/PaperLibrary.tsx`
- `web/src/components/WorkspaceShell.tsx`
- `web/src/components/WorkspaceShell.test.tsx`
- `web/src/components/PdfReader.tsx`
- `web/src/components/PdfReader.test.tsx`
- `web/src/components/RightPanel.tsx`
- `web/src/components/AgentPanel.tsx`
- `web/src/components/GraphPanel.tsx`
- `web/src/components/NotesPanel.tsx`
- `web/src/workspace/types.ts`
- `web/src/workspace/reducer.ts`
- `web/src/workspace/reducer.test.ts`
- `web/src/workspace/usePaperWorkspace.ts`
- `web/src/workspace/usePaperWorkspace.test.tsx`
- `web/src/styles.css`
- `docs/pdf-annotations.md`

---

### Task 1: 修复可重复安装并扩展 API 类型与 SSE 客户端

**Files:**
- Modify: `web/package-lock.json`
- Create: `web/src/api/sse.ts`
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/api/client.test.ts`
- Test: `web/src/api/sse.test.ts`

**Interfaces:**
- `ApiError` gains `code: string | null`
- `paperApi` gains model profile, annotation, note edit/delete, selection assist and paper delete methods
- `streamSelectionAssist(paperId: string, input: SelectionAssistInput, signal: AbortSignal): AsyncGenerator<SelectionAssistEvent>`

- [ ] **Step 1: 修复 lockfile 元数据并验证全新安装**

Run from `web`: `npm install --package-lock-only --ignore-scripts`

Expected: exit 0，lockfile 补齐 `@napi-rs/canvas` 平台可选包记录。

Run from `web`: `npm ci --ignore-scripts`

Expected: exit 0，不再出现 lockfile missing entries。

- [ ] **Step 2: 写 API 与 SSE 失败测试**

```typescript
it('parses split utf-8 sse chunks without losing chinese text', async () => {
  const bytes = new TextEncoder().encode(
    'event: delta\ndata: {"text":"论文解释"}\n\nevent: completed\ndata: {"note":{"id":"note-a"}}\n\n',
  );
  const stream = streamFromChunks([bytes.slice(0, 19), bytes.slice(19, 31), bytes.slice(31)]);
  const events = await collect(parseSse(stream));
  expect(events).toEqual([
    { event: 'delta', data: { text: '论文解释' } },
    { event: 'completed', data: { note: { id: 'note-a' } } },
  ]);
});

it('keeps stable api error code and chinese detail', async () => {
  server.use(http.delete('/api/papers/paper-a', () => HttpResponse.json(
    { code: 'PAPER_BUSY', detail: '论文正在处理中。' },
    { status: 409 },
  )));
  await expect(paperApi.deletePaper('paper-a', 'paper-a')).rejects.toMatchObject({
    status: 409,
    code: 'PAPER_BUSY',
    message: '论文正在处理中。',
  });
});
```

- [ ] **Step 3: 运行测试并确认失败**

Run from `web`: `npm test -- src/api/client.test.ts src/api/sse.test.ts`

Expected: FAIL，缺少 SSE parser 和新 API 方法。

- [ ] **Step 4: 定义前端契约类型**

在 `api/types.ts` 增加并实际导出：

```typescript
export interface ModelSnapshot {
  profile_id: string;
  display_name: string;
  base_url: string;
  model_name: string;
  revision: number;
}

export interface ModelProfile {
  id: string;
  display_name: string;
  base_url: string;
  model_name: string;
  enabled: boolean;
  is_default: boolean;
  revision: number;
  has_api_key: boolean;
  api_key_mask: string | null;
  capabilities: {
    basic_chat: boolean;
    structured_output: boolean;
    tool_calling: boolean;
    checked_at: string | null;
  };
}

export interface TextAnchorDraft {
  quote: string;
  page_number: number;
  rects: Array<{ order: number; x0: number; y0: number; x1: number; y1: number }>;
  element_id?: string;
}

export type SelectionAssistAction = 'explain' | 'translate';

export interface SelectionAssistInput extends TextAnchorDraft {
  action: SelectionAssistAction;
  model_profile_id: string;
  request_id: string;
}

export type SelectionAssistEvent =
  | { event: 'started'; data: { request_id: string } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'completed'; data: { note: Note } }
  | { event: 'error'; data: { code: string; detail: string } };

export type ModelProfileCreateInput = Pick<
  ModelProfile,
  'display_name' | 'base_url' | 'model_name' | 'enabled' | 'is_default'
> & { api_key?: string };

export type ModelProfileUpdateInput = Partial<ModelProfileCreateInput> & {
  clear_api_key?: boolean;
};
```

同步扩展 `Note`、`Highlight`、`AgentMessage`、`PaperSummary` 的模型与笔记引用字段。

- [ ] **Step 5: 实现通用请求和 SSE 解析**

`jsonRequest()` 支持 `POST | PATCH | DELETE`。`request()` 对 204 返回 `undefined`，解析 `{code, detail}`。SSE parser 使用一个持久 `TextDecoder`、按空行分帧，只接受 `started|delta|completed|error`，未知事件抛出安全 `ApiError`。

`streamSelectionAssist()` 使用 fetch POST，验证 `content-type`，在调用者 AbortSignal 取消时停止 reader 并抛出 `AbortError`。

- [ ] **Step 6: 运行 API 测试并提交**

Run from `web`: `npm test -- src/api/client.test.ts src/api/sse.test.ts`

Expected: PASS。

```bash
git add web/package-lock.json web/src/api/types.ts web/src/api/client.ts web/src/api/client.test.ts web/src/api/sse.ts web/src/api/sse.test.ts
git commit -m "feat: add browser api contracts for reader tools"
```

---

### Task 2: 实现模型档案状态、设置弹窗与聊天框选择器

**Files:**
- Create: `web/src/modelProfiles/useModelProfiles.ts`
- Create: `web/src/modelProfiles/useModelProfiles.test.tsx`
- Create: `web/src/components/ModelSelector.tsx`
- Create: `web/src/components/ModelSelector.test.tsx`
- Create: `web/src/components/ModelSettingsDialog.tsx`
- Create: `web/src/components/ModelSettingsDialog.test.tsx`
- Modify: `web/src/App.tsx`
- Test: `web/src/App.test.tsx`

**Interfaces:**
- `useModelProfiles(paperId)` returns profiles, selectedProfileId, selectedProfile, settings actions and loading/error state
- Persists key: `paper-agent:selected-model:<paper-id>`
- `ModelSelector` emits an active profile ID; it never creates a conversation

- [ ] **Step 1: 写默认回退和切换失败测试**

```typescript
it('falls back from stale paper preference to the enabled default profile', async () => {
  localStorage.setItem('paper-agent:selected-model:paper-a', 'deleted-profile');
  server.use(http.get('/api/model-profiles', () => HttpResponse.json([
    modelProfile({ id: 'qwen', is_default: true, enabled: true }),
    modelProfile({ id: 'deepseek', is_default: false, enabled: true }),
  ])));
  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));
});

it('switches the selected model without clearing the current conversation', async () => {
  const onChange = vi.fn();
  render(<ModelSelector profiles={profiles} value="qwen" onChange={onChange} />);
  await userEvent.selectOptions(screen.getByLabelText('当前模型'), 'deepseek');
  expect(onChange).toHaveBeenCalledWith('deepseek');
});
```

设置弹窗测试覆盖新增、编辑时密钥留空不覆盖、清除密钥、能力测试三项中文状态、修订冲突后刷新、删除当前模型后回退。

- [ ] **Step 2: 运行测试并确认失败**

Run from `web`: `npm test -- src/modelProfiles src/components/ModelSelector.test.tsx src/components/ModelSettingsDialog.test.tsx`

Expected: FAIL，新模块不存在。

- [ ] **Step 3: 实现模型选择 hook**

选择顺序严格为：有效本地偏好 → `is_default` → 第一个 enabled → `null`。profile 列表变化时重新校验；用户选择后写 localStorage。hook 暴露：

```typescript
type ModelProfilesState = {
  profiles: ModelProfile[];
  selectedProfileId: string | null;
  selectedProfile: ModelProfile | null;
  loading: boolean;
  error: string | null;
  selectProfile(id: string): void;
  refresh(): Promise<void>;
  createProfile(input: ModelProfileCreateInput): Promise<ModelProfile>;
  updateProfile(id: string, revision: number, input: ModelProfileUpdateInput): Promise<ModelProfile>;
  deleteProfile(id: string, revision: number): Promise<void>;
  testProfile(id: string): Promise<ModelProfile>;
};
```

- [ ] **Step 4: 实现中文设置弹窗**

使用原生 `<dialog>` 或有完整 focus trap 的自定义 modal。字段为`配置名称`、`服务地址`、`模型名称`、`API 密钥（可选）`、`设为默认`。能力结果显示`基础对话`、`结构化输出`、`工具调用`。删除使用二次确认；关闭后焦点回到“模型设置”。

- [ ] **Step 5: 运行组件测试并提交**

Run from `web`: `npm test -- src/modelProfiles src/components/ModelSelector.test.tsx src/components/ModelSettingsDialog.test.tsx src/App.test.tsx`

Expected: PASS。

```bash
git add web/src/modelProfiles web/src/components/ModelSelector.tsx web/src/components/ModelSelector.test.tsx web/src/components/ModelSettingsDialog.tsx web/src/components/ModelSettingsDialog.test.tsx web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: configure and select multiple vllm models"
```

---

### Task 3: 重构论文库、顶栏和永久删除体验

**Files:**
- Create: `web/src/components/ConfirmDeleteDialog.tsx`
- Create: `web/src/components/ConfirmDeleteDialog.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/components/PaperLibrary.tsx`
- Modify: `web/src/components/WorkspaceShell.tsx`

**Interfaces:**
- App has explicit `library` and `reader` views
- `onPaperDeleted(paperId)` clears active paper and removes it from library
- Delete calls `paperApi.deletePaper(paper.id, paper.id)` only after confirmation

- [ ] **Step 1: 写论文库与删除失败测试**

```typescript
it('uses a focused library view and returns there after permanent deletion', async () => {
  render(<App />);
  expect(await screen.findByRole('heading', { name: '论文库' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: /打开 routing-paper\.pdf/ }));
  await userEvent.click(screen.getByRole('button', { name: '论文操作' }));
  await userEvent.click(screen.getByRole('menuitem', { name: '删除论文' }));
  expect(screen.getByText('PDF、解析结果、知识图谱、高亮、笔记和对话将被永久删除。')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: '确认永久删除' }));
  expect(await screen.findByRole('heading', { name: '论文库' })).toBeVisible();
  expect(screen.queryByText('routing-paper.pdf')).not.toBeInTheDocument();
});
```

覆盖：取消不发送请求、409 busy 显示中文并保留阅读页、删除中按钮禁用、从论文库卡片也可触发、返回论文库不丢失论文列表。

- [ ] **Step 2: 运行测试并确认失败**

Run from `web`: `npm test -- src/App.test.tsx src/components/ConfirmDeleteDialog.test.tsx`

Expected: FAIL，当前仍为常驻侧栏且无删除入口。

- [ ] **Step 3: 重构 App 信息架构**

未打开论文时只渲染完整论文库；打开后只渲染阅读工作区。顶栏中文操作固定为：`返回论文库`、论文标题、处理状态、`模型设置`、`论文操作`。`PaperLibrary` 由 `<aside>` 改为 `<section>` 主视图，上传和论文条目使用中文。

- [ ] **Step 4: 实现删除确认弹窗**

弹窗列出将删除的数据，不要求用户输入标题，但按钮使用危险色且文案完整。请求成功关闭弹窗、清空 active paper、刷新 library；失败保留弹窗并显示 `ApiError.message`。弹窗 `Esc` 只取消，不触发删除。

- [ ] **Step 5: 运行测试并提交**

Run from `web`: `npm test -- src/App.test.tsx src/components/ConfirmDeleteDialog.test.tsx`

Expected: PASS。

```bash
git add web/src/App.tsx web/src/App.test.tsx web/src/components/PaperLibrary.tsx web/src/components/WorkspaceShell.tsx web/src/components/ConfirmDeleteDialog.tsx web/src/components/ConfirmDeleteDialog.test.tsx
git commit -m "feat: focus the chinese paper library and deletion flow"
```

---

### Task 4: 实现连续 PDF 页面、虚拟化和 TextLayer

**Files:**
- Modify: `web/src/pdfjs.ts`
- Modify: `web/src/pdfjs.test.ts`
- Create: `web/src/components/PdfPageView.tsx`
- Create: `web/src/components/PdfPageView.test.tsx`
- Modify: `web/src/components/PdfReader.tsx`
- Modify: `web/src/components/PdfReader.test.tsx`

**Interfaces:**
- One PDF loading task per `paperId`
- `PdfPageView` renders canvas and PDF.js `TextLayer` with the same viewport
- Pages use IntersectionObserver with 1200px vertical root margin; unmounted content keeps an aspect-ratio page shell

- [ ] **Step 1: 写文字层与虚拟页失败测试**

```typescript
it('renders canvas and text layer from the same viewport', async () => {
  render(<PdfPageView document={pdfDocument} page={pageMeta} active overlays={[]} />);
  await waitFor(() => expect(pdfPage.render).toHaveBeenCalledOnce());
  expect(textLayerConstructor).toHaveBeenCalledWith(expect.objectContaining({
    container: screen.getByTestId('pdf-text-layer-1'),
    viewport: pdfPage.viewport,
  }));
  expect(textLayerRender).toHaveBeenCalledOnce();
});

it('keeps an aspect ratio page shell until a page enters the overscan area', () => {
  render(<PdfReader paperId="paper-a" pages={manyPages} />);
  expect(screen.getByTestId('pdf-page-shell-20')).toHaveStyle({ aspectRatio: '612 / 792' });
  expect(pdfDocument.getPage).not.toHaveBeenCalledWith(20);
});
```

覆盖：加载任务切换取消、canvas 高 DPI、TextLayer cleanup cancel、页码定位使目标页进入 active 集合、无文本内容显示“该页无法选择文字”但仍显示画布。

- [ ] **Step 2: 运行测试并确认失败**

Run from `web`: `npm test -- src/pdfjs.test.ts src/components/PdfPageView.test.tsx src/components/PdfReader.test.tsx`

Expected: FAIL，没有 TextLayer 和连续页面组件。

- [ ] **Step 3: 导出并渲染 PDF.js TextLayer**

`pdfjs.ts` 导出 `getDocument` 与 `TextLayer`。页面 effect：读取 page → 按容器宽度计算 viewport → canvas 使用 `devicePixelRatio` backing store → `new TextLayer({ textContentSource: page.streamTextContent(), container, viewport })` → `render()`。cleanup 取消 canvas task、text layer 和 ResizeObserver。

- [ ] **Step 4: 实现轻量虚拟化**

PdfReader 渲染全部 page shell 保持滚动高度，每个 shell 通过一个共享 IntersectionObserver 设置 active。active 页加前后各一页 overscan；目标证据或笔记页强制 active。使用 `scrollIntoView({block:'center'})`，减少动态效果时使用 `behavior:'auto'`。

- [ ] **Step 5: 运行 PDF 测试并提交**

Run from `web`: `npm test -- src/pdfjs.test.ts src/components/PdfPageView.test.tsx src/components/PdfReader.test.tsx`

Expected: PASS。

```bash
git add web/src/pdfjs.ts web/src/pdfjs.test.ts web/src/components/PdfPageView.tsx web/src/components/PdfPageView.test.tsx web/src/components/PdfReader.tsx web/src/components/PdfReader.test.tsx
git commit -m "feat: render selectable continuous pdf pages"
```

---

### Task 5: 将浏览器选区转换为锚点并实现高亮

**Files:**
- Create: `web/src/components/pdfSelection.ts`
- Create: `web/src/components/pdfSelection.test.ts`
- Create: `web/src/components/AnnotationOverlay.tsx`
- Create: `web/src/components/AnnotationOverlay.test.tsx`
- Create: `web/src/components/SelectionToolbar.tsx`
- Create: `web/src/components/SelectionToolbar.test.tsx`
- Modify: `web/src/components/PdfPageView.tsx`
- Modify: `web/src/components/PdfReader.tsx`
- Modify: `web/src/workspace/types.ts`
- Modify: `web/src/workspace/reducer.ts`
- Modify: `web/src/workspace/usePaperWorkspace.ts`
- Test: corresponding workspace tests

**Interfaces:**
- `selectionToAnchorDraft(selection: Selection, reader: HTMLElement) -> SelectionResult`
- Valid result contains `draft` and viewport toolbar rect
- Workspace actions: `selection/set`, `selection/clear`, `highlights/loaded`, `highlight/created`, `highlight/deleted`

- [ ] **Step 1: 写坐标转换失败测试**

```typescript
it('normalizes multiple client rects inside one pdf page', () => {
  const pageRect = domRect({ left: 100, top: 200, width: 600, height: 800 });
  const selection = fakeSelection('selected text', [
    domRect({ left: 160, top: 280, width: 300, height: 20 }),
    domRect({ left: 160, top: 305, width: 180, height: 20 }),
  ], pageElement(2, pageRect));
  const result = selectionToAnchorDraft(selection, readerElement());
  expect(result).toMatchObject({
    ok: true,
    draft: {
      quote: 'selected text',
      page_number: 2,
      rects: [
        { order: 0, x0: 0.1, y0: 0.1, x1: 0.6, y1: 0.125 },
        { order: 1, x0: 0.1, y0: 0.13125, x1: 0.4, y1: 0.15625 },
      ],
    },
  });
});
```

覆盖：跨页返回 `cross_page`、空白/仅标点返回 `empty`、选区不在 text layer 返回 `outside_reader`、矩形裁剪到 `[0,1]`、零面积 rect 丢弃。

- [ ] **Step 2: 运行测试并确认失败**

Run from `web`: `npm test -- src/components/pdfSelection.test.ts src/components/SelectionToolbar.test.tsx src/components/AnnotationOverlay.test.tsx`

Expected: FAIL，新模块不存在。

- [ ] **Step 3: 实现选区采集与工具栏**

转换结果使用显式联合类型：

```typescript
export type SelectionResult =
  | { ok: true; draft: TextAnchorDraft; toolbarRect: DOMRect }
  | { ok: false; reason: 'empty' | 'cross_page' | 'outside_reader' };
```

在 reader 的 `pointerup` 和键盘 selectionchange 后读取 `window.getSelection()`。要求 anchor/focus 都位于同一 `[data-pdf-page]`。每个 `Range.getClientRects()` 相对 `.pdf-page__surface` 归一化。工具栏使用 `position: fixed`，先居中于选区最后一行上方，再夹在 viewport 12px 内。

工具栏按钮顺序固定：`解释`、`翻译`、`记笔记`、`问助手`、`高亮`。`Esc` 清除临时 selection，返回焦点到 PDF 页面。

- [ ] **Step 4: 实现高亮持久化与覆盖层**

打开论文时并行加载 annotations。点击高亮调用 `createHighlight(paperId, {...draft, color: 'yellow', request_id: crypto.randomUUID()})`，成功后 reducer 添加；失败保留选区并显示中文错误。`AnnotationOverlay` 使用百分比定位每个 rect；点击高亮打开包含`记笔记`和`删除高亮`的小菜单。

- [ ] **Step 5: 运行选择、高亮和 workspace 测试**

Run from `web`: `npm test -- src/components/pdfSelection.test.ts src/components/SelectionToolbar.test.tsx src/components/AnnotationOverlay.test.tsx src/workspace`

Expected: PASS。

- [ ] **Step 6: 提交选区和高亮**

```bash
git add web/src/components/pdfSelection.ts web/src/components/pdfSelection.test.ts web/src/components/AnnotationOverlay.tsx web/src/components/AnnotationOverlay.test.tsx web/src/components/SelectionToolbar.tsx web/src/components/SelectionToolbar.test.tsx web/src/components/PdfPageView.tsx web/src/components/PdfReader.tsx web/src/workspace
git commit -m "feat: select and highlight paper text"
```

---

### Task 6: 实现解释/翻译浮层和锚定笔记

**Files:**
- Create: `web/src/components/InlineAssistantPopover.tsx`
- Create: `web/src/components/InlineAssistantPopover.test.tsx`
- Modify: `web/src/components/PdfReader.tsx`
- Modify: `web/src/components/NotesPanel.tsx`
- Modify: `web/src/workspace/reducer.ts`
- Modify: `web/src/workspace/usePaperWorkspace.ts`
- Test: corresponding tests

**Interfaces:**
- `InlineAssistantPopover` states: idle, streaming, completed, cancelled, failed
- `runSelectionAssist(action, draft, modelProfileId, requestId, signal)` dispatches completed note
- Notes filters: all, manual, explanation, translation

- [ ] **Step 1: 写流式浮层和自动笔记失败测试**

```typescript
it('streams explanation near the selection and appends the completed note', async () => {
  server.use(http.post('/api/papers/paper-a/selection-assists', () => sseResponse([
    ['started', { request_id: 'assist-a' }],
    ['delta', { text: '这是' }],
    ['delta', { text: '路由机制。' }],
    ['completed', { note: generatedNote({ body: '这是路由机制。' }) }],
  ])));
  render(<ReaderHarness />);
  await selectPaperText('routing mechanism');
  await userEvent.click(screen.getByRole('button', { name: '解释' }));
  expect(await screen.findByText('这是路由机制。')).toBeVisible();
  expect(screen.getByText('已存入笔记')).toBeVisible();
  await userEvent.click(screen.getByRole('tab', { name: '笔记' }));
  expect(screen.getByText('这是路由机制。')).toBeVisible();
});
```

覆盖：Abort 后保留可复制临时文本但不加 note、error 允许重试且复用 request ID、没有当前模型时禁用解释/翻译、翻译笔记筛选、手写笔记锚定、编辑和删除。

- [ ] **Step 2: 运行测试并确认失败**

Run from `web`: `npm test -- src/components/InlineAssistantPopover.test.tsx src/components/NotesPanel.test.tsx src/workspace/usePaperWorkspace.test.tsx`

Expected: FAIL，缺少浮层和扩展 note actions。

- [ ] **Step 3: 实现浮层状态机**

开始时创建一个 request ID 和 AbortController；`delta` 追加文本；`completed` 使用服务端 note 替换临时状态并 dispatch `notes/created`；`error` 显示安全 message。关闭 streaming 浮层先 abort；关闭 completed 只清除 UI，不删除 note。

- [ ] **Step 4: 扩展中文笔记面板**

顶部筛选为`全部`、`我的笔记`、`解释`、`翻译`。卡片显示原文摘录、页码、类型、模型、AI 生成/用户已编辑。编辑使用 `updated_at`，409 时提示刷新；点击原文调用统一 source target 定位；删除 note 不自动删除高亮。

- [ ] **Step 5: 运行测试并提交**

Run from `web`: `npm test -- src/components/InlineAssistantPopover.test.tsx src/components/NotesPanel.test.tsx src/workspace`

Expected: PASS。

```bash
git add web/src/components/InlineAssistantPopover.tsx web/src/components/InlineAssistantPopover.test.tsx web/src/components/PdfReader.tsx web/src/components/NotesPanel.tsx web/src/components/NotesPanel.test.tsx web/src/workspace
git commit -m "feat: explain selections into anchored notes"
```

---

### Task 7: 重构右侧工作台、固定聊天框和当前模型调用

**Files:**
- Create: `web/src/components/ChatComposer.tsx`
- Create: `web/src/components/ChatComposer.test.tsx`
- Create: `web/src/components/ResizableSplit.tsx`
- Create: `web/src/components/ResizableSplit.test.tsx`
- Modify: `web/src/components/RightPanel.tsx`
- Modify: `web/src/components/AgentPanel.tsx`
- Modify: `web/src/components/GraphPanel.tsx`
- Modify: `web/src/components/WorkspaceShell.tsx`
- Modify: `web/src/workspace/usePaperWorkspace.ts`
- Modify: component and workspace tests

**Interfaces:**
- Right tabs: `论文助手 | 知识图谱 | 笔记`
- `ChatComposer` always receives `selectedModelProfileId`
- `askAgent`, `buildCoreGraph`, `buildDeepGraph` require current model ID and new request ID
- `问助手` attaches current TextAnchorDraft chip without sending

- [ ] **Step 1: 写当前模型和同会话切换失败测试**

```typescript
it('uses the composer model for chat, selection assist, and graph builds', async () => {
  render(<WorkspaceHarness selectedModel="qwen" />);
  await userEvent.type(screen.getByLabelText('向论文助手提问'), '解释方法');
  await userEvent.click(screen.getByRole('button', { name: '发送' }));
  expect(agentRequest()).toMatchObject({ model_profile_id: 'qwen' });

  await userEvent.selectOptions(screen.getByLabelText('当前模型'), 'deepseek');
  await userEvent.click(screen.getByRole('tab', { name: '知识图谱' }));
  await userEvent.click(screen.getByRole('button', { name: '构建核心图谱' }));
  expect(graphRequest()).toMatchObject({ model_profile_id: 'deepseek' });
  expect(currentConversationId()).toBe('conversation-a');
});
```

覆盖：问助手附件 chip、移除附件、每条消息模型 badge、笔记引用定位、无模型禁用发送和图谱、图谱显示构建模型、聊天框在三个 tab 都存在。

- [ ] **Step 2: 运行测试并确认失败**

Run from `web`: `npm test -- src/components/ChatComposer.test.tsx src/components/WorkspaceShell.test.tsx src/components/GraphPanel.test.tsx src/workspace/usePaperWorkspace.test.tsx`

Expected: FAIL，当前 API 调用不传模型 ID。

- [ ] **Step 3: 拆分显示面板与固定 composer**

`AgentPanel` 只显示会话；`ChatComposer` 位于 RightPanel tab content 之外的固定底部。RightPanel 在 tab body 渲染 Agent/Graph/Notes，composer 始终保留。模式选择保留但中文化为`仅基于论文`和`允许背景知识`。

- [ ] **Step 4: 统一当前模型调用**

`usePaperWorkspace` 的三个调用签名改为：

```typescript
askAgent(content: string, mode: AgentMode, modelProfileId: string, selection?: TextAnchorDraft): Promise<AgentMessage | null>
buildCoreGraph(modelProfileId: string): Promise<PaperGraph | null>
buildDeepGraph(modelProfileId: string): Promise<PaperGraph | null>
```

每次内部生成 `crypto.randomUUID()`。改变 modelProfileId 不 dispatch conversation reset。问助手只设置 composer attachment 和 focus。

- [ ] **Step 5: 实现可调整双栏与小屏切换**

`ResizableSplit` 默认 62%，限制 45%–78%，separator 支持 pointer drag 和左右方向键，每次 2%，保存 `paper-agent:reader-split`。宽度低于 880px 时隐藏 separator，显示`论文`/`工具`切换按钮并保留两侧状态。

- [ ] **Step 6: 运行工作区测试并提交**

Run from `web`: `npm test -- src/components/ChatComposer.test.tsx src/components/ResizableSplit.test.tsx src/components/WorkspaceShell.test.tsx src/components/GraphPanel.test.tsx src/workspace`

Expected: PASS。

```bash
git add web/src/components/ChatComposer.tsx web/src/components/ChatComposer.test.tsx web/src/components/ResizableSplit.tsx web/src/components/ResizableSplit.test.tsx web/src/components/RightPanel.tsx web/src/components/AgentPanel.tsx web/src/components/GraphPanel.tsx web/src/components/WorkspaceShell.tsx web/src/components/WorkspaceShell.test.tsx web/src/workspace
git commit -m "feat: use one model across the reading workspace"
```

---

### Task 8: 应用视觉系统、中文化和可访问性预检

**Files:**
- Modify: `web/src/styles.css`
- Modify: all user-facing component tests
- Modify: `docs/pdf-annotations.md`
- Test: all frontend tests and production build

**Interfaces:**
- CSS tokens define warm neutral workspace, white paper, ink text, indigo accent and yellow highlight
- All interactive states have Chinese copy, visible focus and reduced-motion behavior

- [ ] **Step 1: 按 design-taste-frontend 做 redesign preflight**

在编辑 CSS 前记录并检查：页面类型为桌面中文研究工具；阅读证据优先；参考图只提供布局骨架；禁止营销式巨型标题、装饰渐变、英文 eyebrow、过度圆角和卡片嵌套。

- [ ] **Step 2: 写中文化与可访问性失败测试**

```typescript
it('exposes chinese landmarks and keeps technical names only where needed', async () => {
  render(<App />);
  expect(await screen.findByRole('heading', { name: '论文库' })).toBeVisible();
  expect(screen.getByLabelText('上传 PDF')).toBeVisible();
  expect(screen.queryByText('Paper library')).not.toBeInTheDocument();
  expect(screen.queryByText('Build core graph')).not.toBeInTheDocument();
});

it('supports keyboard resizing and escape-closing the selection toolbar', async () => {
  render(<WorkspaceHarness />);
  const separator = screen.getByRole('separator', { name: '调整论文与工具宽度' });
  separator.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(separator).toHaveAttribute('aria-valuenow', '64');
  await openSelectionToolbar();
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('toolbar', { name: '选中文字操作' })).not.toBeInTheDocument();
});
```

- [ ] **Step 3: 运行测试并确认失败**

Run from `web`: `npm test`

Expected: FAIL，现有英文文本和布局断言尚未更新。

- [ ] **Step 4: 实现视觉变量和状态样式**

根变量固定为：暖灰 `--workspace: #f2f0eb`、白纸 `--paper: #fffefb`、墨色 `--ink: #1d2430`、次级文字 `--muted: #667085`、靛蓝 `--accent: #4f46e5`、边线 `--line: #d8d5cf`、高亮 `--highlight: rgba(250, 204, 21, .32)`。正文中文字体栈包含 `PingFang SC`、`Microsoft YaHei`、系统 sans-serif。

为 loading、empty、error、disabled、streaming、saved、selected、deleting 提供视觉状态；focus ring 至少 2px；危险删除只使用一个明确红色。`@media (prefers-reduced-motion: reduce)` 关闭平滑滚动和非必要过渡。

- [ ] **Step 5: 更新 PDF 批注文档的前端采集章节**

补充 TextLayer/Canvas 同 viewport、IntersectionObserver overscan、selection rect 归一化、跨页拒绝、缩放恢复和证据层/批注层颜色区别。

- [ ] **Step 6: 运行完整前端验证**

Run from `web`: `npm test`

Expected: 全部测试 PASS，0 failures。

Run from `web`: `npm run build`

Expected: TypeScript 与 Vite build exit 0。

- [ ] **Step 7: 提交视觉与中文化**

```bash
git add web/src docs/pdf-annotations.md
git commit -m "feat: polish the chinese paper reading workspace"
```
