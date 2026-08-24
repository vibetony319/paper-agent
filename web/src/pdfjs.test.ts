import { expect, it, vi } from 'vitest';

const pdf = vi.hoisted(() => ({
  getDocument: vi.fn(),
  TextLayer: vi.fn(),
  workerOptions: {} as { workerSrc?: string },
}));

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: pdf.workerOptions,
  getDocument: pdf.getDocument,
  TextLayer: pdf.TextLayer,
}));

it('configures the Vite-resolved PDF.js worker once for production consumers', async () => {
  const pdfjs = await import('./pdfjs');

  expect(pdfjs.getDocument).toBe(pdf.getDocument);
  expect(pdfjs.TextLayer).toBe(pdf.TextLayer);
  expect(pdf.workerOptions.workerSrc).toMatch(/pdf\.worker\.min\.mjs$/);
});
