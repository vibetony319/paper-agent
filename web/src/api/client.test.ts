import { HttpResponse, http } from 'msw';
import { expect, it, vi } from 'vitest';

import { paperApi } from './client';
import { server } from '../test/server';

it('sends paper upload as FormData and preserves a safe API error', async () => {
  server.use(http.post('/api/papers', () => HttpResponse.json(
    { detail: 'Invalid PDF upload.' }, { status: 422 },
  )));

  await expect(paperApi.upload(new File(['not-pdf'], 'paper.pdf')))
    .rejects.toMatchObject({ status: 422, message: 'Invalid PDF upload.' });
});

it('forwards an AbortSignal to selected paper loads', async () => {
  const controller = new AbortController();
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(
    new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
  ));

  try {
    await Promise.all([
      paperApi.getDocument('paper id', { signal: controller.signal }),
      paperApi.getGraph('paper id', { signal: controller.signal }),
      paperApi.getNotes('paper id', { signal: controller.signal }),
    ]);

    expect(fetchSpy).toHaveBeenNthCalledWith(1, '/api/papers/paper%20id/document', {
      signal: controller.signal,
    });
    expect(fetchSpy).toHaveBeenNthCalledWith(2, '/api/papers/paper%20id/graph', {
      signal: controller.signal,
    });
    expect(fetchSpy).toHaveBeenNthCalledWith(3, '/api/papers/paper%20id/notes', {
      signal: controller.signal,
    });
  } finally {
    fetchSpy.mockRestore();
  }
});

it('builds a relative original-PDF URL', () => {
  expect(paperApi.getSourceUrl('paper id')).toBe('/api/papers/paper%20id/source');
});
