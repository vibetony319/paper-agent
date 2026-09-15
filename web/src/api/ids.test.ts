import { expect, it, vi } from 'vitest';

import { newRequestId } from './ids';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

it('uses crypto.randomUUID when the page is a secure context', () => {
  const randomUUID = vi.fn(() => '11111111-2222-3333-4444-555555555555');
  vi.stubGlobal('crypto', { randomUUID });

  expect(newRequestId()).toBe('11111111-2222-3333-4444-555555555555');
  vi.unstubAllGlobals();
});

it('returns a UUID v4 where only getRandomValues is available', () => {
  // Plain http on a LAN address: no randomUUID, but getRandomValues works.
  vi.stubGlobal('crypto', {
    getRandomValues: (array: Uint8Array) => {
      for (let i = 0; i < array.length; i += 1) {
        array[i] = Math.floor(Math.random() * 256);
      }
      return array;
    },
  });

  expect(newRequestId()).toMatch(UUID_V4);
  vi.unstubAllGlobals();
});

it('still returns a unique UUID v4 where crypto is missing entirely', () => {
  // Breaks if a reader opened over plain http cannot send a request at all:
  // the backend rejects any request_id that is not a UUID.
  vi.stubGlobal('crypto', {});

  const first = newRequestId();
  const second = newRequestId();

  expect(first).toMatch(UUID_V4);
  expect(second).toMatch(UUID_V4);
  expect(first).not.toBe(second);
  vi.unstubAllGlobals();
});
