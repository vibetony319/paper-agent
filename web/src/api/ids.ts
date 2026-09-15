const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/**
 * Return a stable request id for one write or stream request.
 *
 * ``crypto.randomUUID`` only exists in a secure context, so a reader opened
 * over plain http on a LAN address would throw on every click instead of
 * sending the request. ``crypto.getRandomValues`` still works there, and the
 * fallback must still be a UUID because the backend schemas validate
 * ``request_id`` as one.
 */
export function newRequestId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi !== undefined && typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (cryptoApi !== undefined && typeof cryptoApi.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'));
  const id = `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`;
  if (!UUID_V4.test(id)) {
    throw new Error('newRequestId produced a malformed UUID');
  }
  return id;
}
