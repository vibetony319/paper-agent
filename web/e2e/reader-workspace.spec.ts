import AxeBuilder from '@axe-core/playwright';
import { resolve } from 'node:path';
import { test, expect, api, filename, explanation, showPane, selectText, noHorizontalOverflow } from './testApi';

test('选文、高亮、自动笔记、会话内换模型、图谱和永久删除', async ({ page, request, paperId }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await selectText(page, /^We introduce/, /^experts\.$/);
  const toolbar = page.getByRole('toolbar', { name: /^已选择：/ });
  await expect(toolbar).toHaveAttribute('aria-label', /routing load balancing loss/);
  await toolbar.getByRole('button', { name: '解释', exact: true }).click();
  const assist = page.getByRole('region', { name: '解释选区' });
  await expect(assist.getByText('已存入笔记', { exact: true })).toBeVisible();
  await expect(assist.getByText(explanation, { exact: true })).toBeVisible();
  const bounds = await assist.boundingBox();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  await assist.getByRole('button', { name: '关闭', exact: true }).click();
  await selectText(page, /^We introduce/, /^experts\.$/);
  const highlighting = page.waitForResponse((r) => r.url().endsWith('/highlights') && r.request().method() === 'POST');
  await toolbar.getByRole('button', { name: '高亮', exact: true }).click();
  expect((await highlighting).status()).toBe(201);
  await expect(page.getByRole('button', { name: /^高亮：/ }).first()).toBeVisible();
  await showPane(page, '工具');
  await page.getByRole('tab', { name: '笔记', exact: true }).click();
  await expect(page.getByRole('tabpanel').getByText(explanation, { exact: true })).toBeVisible();
  await noHorizontalOverflow(page);
  await page.screenshot({ path: info.outputPath('reader-workspace.png') });
  if (process.env.UPDATE_README_SCREENSHOT === '1' && info.project.name === 'desktop-chromium') {
    await page.screenshot({ path: resolve('../docs/assets/reader-workspace.png') });
  }

  await page.getByRole('tab', { name: '论文助手', exact: true }).click();
  const send = async () => {
    await page.getByRole('textbox', { name: '向论文助手提问' }).fill('负载均衡损失如何分配专家？');
    const response = page.waitForResponse((r) => r.url().endsWith('/agent/messages') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    const result = await response;
    expect(result.ok()).toBeTruthy();
    return result.json();
  };
  const first = await send();
  expect(first.model.display_name).toBe('测试 Qwen');
  expect(first.note_references).toHaveLength(1);
  await expect(page.getByRole('button', { name: '笔记：第 1 页' })).toBeVisible();
  await page.getByRole('combobox', { name: '当前模型' }).selectOption({ label: '测试 DeepSeek' });
  const second = await send();
  expect(second.conversation_id).toBe(first.conversation_id);
  expect(second.model.display_name).toBe('测试 DeepSeek');
  await expect(page.getByRole('tabpanel').getByText('测试 DeepSeek', { exact: true })).toBeVisible();

  // Use another passage so a persisted highlight does not intercept the drag.
  await selectText(page, /^Sparse expert models/);
  await toolbar.getByRole('button', { name: '翻译', exact: true }).click();
  const translation = page.getByRole('region', { name: '翻译选区' });
  await expect(translation.getByText('已存入笔记', { exact: true })).toBeVisible();
  await translation.getByRole('button', { name: '关闭', exact: true }).click();
  await selectText(page, /^Sparse expert models/);
  await toolbar.getByRole('button', { name: '记笔记', exact: true }).click();
  const manual = page.getByRole('form', { name: '为选区记笔记' });
  await manual.getByRole('textbox', { name: '选区笔记' }).fill('检查专家负载均衡的实验设置。');
  await manual.getByRole('button', { name: '保存', exact: true }).click();
  await expect(manual).not.toBeVisible();
  await showPane(page, '工具');
  await page.getByRole('tab', { name: '笔记', exact: true }).click();
  await expect(page.getByText('检查专家负载均衡的实验设置。', { exact: true })).toBeVisible();
  await expect(page.getByText('我们提出了一种用于稀疏专家的路由负载均衡损失。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^Sparse expert models/ }).first().click();
  await showPane(page, '论文');
  await expect(page.getByRole('button', { name: '清除证据定位' })).toBeVisible();

  await showPane(page, '工具');
  await page.getByRole('tab', { name: '知识图谱', exact: true }).click();
  await page.getByRole('button', { name: '构建核心图谱', exact: true }).click();
  await expect(page.getByText('构建模型：测试 DeepSeek', { exact: true })).toBeVisible();
  await expect(page.getByText('负载均衡路由', { exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: `打开 ${filename}`, exact: true }).click();
  await expect(page.getByRole('button', { name: /^高亮：/ }).first()).toBeVisible();
  await page.getByRole('button', { name: '论文操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '删除论文' }).click();
  const dialog = page.getByRole('dialog', { name: '删除论文' });
  await expect(dialog.getByText(/PDF、解析结果、知识图谱、高亮、笔记和对话/)).toBeVisible();
  await dialog.getByRole('button', { name: '确认永久删除' }).click();
  await expect(page.getByRole('heading', { name: '论文库', exact: true })).toBeVisible();
  await expect(page.getByText('论文库中还没有论文。')).toBeVisible();
  for (const suffix of ['', '/document', '/source', '/graph', '/annotations', `/agent/conversations/${first.conversation_id}`]) {
    expect((await request.get(`${api}/api/papers/${paperId}${suffix}`)).status()).toBe(404);
  }
  expect(errors).toEqual([]);
});

test('键盘、弹框和桌面小屏可访问性', async ({ page, paperId }, info) => {
  expect(paperId).toBeTruthy();
  await noHorizontalOverflow(page);
  const settings = page.getByRole('button', { name: '模型设置', exact: true });
  await settings.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  const box = await page.getByRole('dialog').boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  const settingsAudit = await new AxeBuilder({ page }).analyze();
  expect(settingsAudit.violations.filter((v) => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(settings).toBeFocused();
  if (info.project.name === 'desktop-chromium') {
    const separator = page.getByRole('separator', { name: '调整论文与工具宽度' });
    const previous = Number(await separator.getAttribute('aria-valuenow'));
    await separator.focus();
    await page.keyboard.press('ArrowLeft');
    await expect(separator).toHaveAttribute('aria-valuenow', String(previous - 2));
  } else {
    await showPane(page, '工具');
    await page.getByRole('combobox', { name: '当前模型' }).selectOption({ label: '测试 DeepSeek' });
    await expect(page.getByRole('combobox', { name: '当前模型' })).toContainText('测试 DeepSeek');
  }
  await noHorizontalOverflow(page);
  const audit = await new AxeBuilder({ page }).analyze();
  expect(audit.violations.filter((v) => ['serious', 'critical'].includes(v.impact ?? ''))).toEqual([]);
});
