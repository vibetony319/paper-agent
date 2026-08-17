import { expect, it } from 'vitest';

import { normalizedBoxStyle, sourceOverlayStyle } from './pdfGeometry';

it('maps a normalized source rectangle to an overlay percentage rectangle', () => {
  expect(normalizedBoxStyle({ x0: 0.125, y0: 0.2, x1: 0.75, y1: 0.3 })).toEqual({
    left: '12.5%',
    top: '20%',
    width: '62.5%',
    height: '10%',
  });
});

it('does not create an overlay for a missing source target', () => {
  expect(sourceOverlayStyle(null)).toBeNull();
});
