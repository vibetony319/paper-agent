import { HttpResponse, http } from 'msw';
import { expect, it } from 'vitest';

import { paperApi } from './client';
import { server } from '../test/server';

it('sends paper upload as FormData and preserves a safe API error', async () => {
  server.use(http.post('/api/papers', () => HttpResponse.json(
    { detail: 'Invalid PDF upload.' }, { status: 422 },
  )));

  await expect(paperApi.upload(new File(['not-pdf'], 'paper.pdf')))
    .rejects.toMatchObject({ status: 422, message: 'Invalid PDF upload.' });
});
