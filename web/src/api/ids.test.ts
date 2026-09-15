import { expect, it, vi } from 'vitest';

import { newRequestId } from './ids';

it('uses crypto.randomUUID when the page is a secure context', () => {
  const randomUUID = vi.fn(() => '11111111-2222-3333-4444-555555555555');
  vi.stubGlobal('crypto', { randomUUID });

  expect(newRequestId()).toBe('11111111-2222-3333-4444-555555555555');
  vi.unstubAllGlobals();
});

it('still returns a unique id where crypto.randomUUID is missing', () => {
  // Breaks if a reader opened over plain http cannot send a request at all.
  vi.stubGlobal('crypto', {});

  const first = newRequestId();
  const second = newRequestId();

  expect(first).toMatch(/^req-/);
  expect(first).not.toBe(second);
  vi.unstubAllGlobals();
});
