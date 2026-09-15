let fallbackCounter = 0;

/**
 * Return a stable request id for one write or stream request.
 *
 * ``crypto.randomUUID`` only exists in a secure context, so a reader opened
 * over plain http on a LAN address would throw on every click instead of
 * sending the request. The fallback only needs to be unique per session.
 */
export function newRequestId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi !== undefined && typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  fallbackCounter += 1;
  return [
    'req',
    Date.now().toString(36),
    fallbackCounter.toString(36),
    Math.random().toString(36).slice(2, 10),
  ].join('-');
}
