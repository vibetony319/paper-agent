import { expect, test as base, type Page } from '@playwright/test';

export const api = 'http://127.0.0.1:8000';
export const filename = '路由负载均衡.pdf';
export const explanation = '这段文字说明作者使用负载均衡损失来分配专家。';

export const test = base.extend<{ paperId: string }>({
  paperId: async ({ page, request }, use) => {
    const fixture = await request.get(`${api}/__e2e__/fixture.pdf`);
    expect(fixture.ok()).toBeTruthy();
    await page.goto('/');
    const upload = page.waitForResponse((r) => r.url().endsWith('/api/papers') && r.request().method() === 'POST');
    await page.getByLabel('上传 PDF').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: await fixture.body() });
    const response = await upload;
    expect(response.status()).toBe(201);
    const { id } = await response.json();
    try {
      await expect(page.getByRole('heading', { name: filename })).toBeVisible();
      await expect(page.getByRole('img', { name: 'PDF 第 1 页', exact: true })).toBeVisible();
      await use(id);
    } finally {
      // This ID came from this test's upload; never clear a whole paper library.
      const deleted = await request.delete(`${api}/api/papers/${id}`, { data: { confirmation: id } });
      expect([204, 404]).toContain(deleted.status());
    }
  },
});

export async function showPane(page: Page, pane: '论文' | '工具') {
  if ((page.viewportSize()?.width ?? 1440) < 768) {
    await page.getByRole('button', { name: pane, exact: true }).click();
  }
}

export async function selectText(page: Page, first: RegExp, last?: RegExp) {
  await showPane(page, '论文');
  // A drag inside an existing selection can move the selection instead of
  // starting a new one. Dismiss it with the reader's supported keyboard action.
  const existingToolbar = page.getByRole('toolbar', { name: /^已选择：/ });
  if (await existingToolbar.isVisible()) {
    await existingToolbar.getByRole('button', { name: '解释', exact: true }).focus();
    await page.keyboard.press('Escape');
    await expect(existingToolbar).not.toBeVisible();
  }
  // The only layout selector: real PDF.js text spans are needed for a mouse drag.
  const spans = page.locator('[data-pdf-page="1"] .pdf-page-view__text-layer span');
  const start = spans.filter({ hasText: first }).first();
  const end = last ? spans.filter({ hasText: last }).last() : start;
  await expect(start).toBeVisible();
  await start.scrollIntoViewIfNeeded();
  const a = await start.boundingBox();
  const b = await end.boundingBox();
  if (!a || !b) throw new Error('PDF text spans have no visible bounds');
  await page.mouse.move(a.x + 1, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width - 1, b.y + b.height / 2, { steps: 15 });
  await page.mouse.up();
  await expect(page.getByRole('toolbar', { name: /^已选择：/ })).toBeVisible();
}

export async function noHorizontalOverflow(page: Page) {
  const sizes = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.width + 1);
}

export { expect };
