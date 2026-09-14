import { test, expect, selectText, showPane } from './testApi';

test('解释弹框保留选区且重复解释翻译可继续使用', async ({ page, paperId }) => {
  expect(paperId).toBeTruthy();
  for (const action of ['解释', '翻译', '解释']) {
    await selectText(page, /^We introduce/, /^experts\.$/);
    await page.getByRole('toolbar').getByRole('button', { name: action, exact: true }).click();
    const popup = page.getByRole('region', { name: `${action}选区` });
    await expect(popup.getByText('已存入笔记', { exact: true })).toBeVisible();
    await popup.locator('p').first().click();
    await expect(popup).toBeVisible();
    await popup.getByRole('button', { name: '关闭', exact: true }).click();
  }
  await showPane(page, '工具');
  await page.getByRole('textbox', { name: '向论文助手提问' }).fill('你好');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText(/你好！我可以帮你概括论文/)).toBeVisible();
});
