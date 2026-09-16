import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

// Default handlers registered here survive afterEach's resetHandlers(), so
// auxiliary reads the workspace fires on its own (like the context-usage
// readout) never trip onUnhandledRequest in unrelated tests.
export const server = setupServer(
  http.get('/api/papers/:paperId/agent/conversations/:conversationId/context-usage', () => HttpResponse.json({
    used_tokens: 1200,
    context_length: null,
    effective_limit: null,
    compaction_threshold: null,
    percent: null,
  })),
);
