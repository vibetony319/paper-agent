import { expect, it } from 'vitest';

import { linkTextLineBox, normalizedBoxStyle, sourceOverlayStyle } from './pdfGeometry';

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

it('expands a link point to the nearby text line without including another column', () => {
  const surface = document.createElement('div');
  const textLayer = document.createElement('div');
  surface.append(textLayer);
  surface.getBoundingClientRect = () => new DOMRect(100, 200, 600, 800);
  const addSpan = (text: string, rect: DOMRect) => {
    const span = document.createElement('span');
    span.textContent = text;
    span.getBoundingClientRect = () => rect;
    textLayer.append(span);
  };
  addSpan('previous paragraph', new DOMRect(150, 360, 250, 18));
  addSpan('2.4.4.', new DOMRect(190, 398, 40, 18));
  addSpan('FP4 Main KV Cache', new DOMRect(236, 398, 130, 18));
  addSpan('other column', new DOMRect(530, 398, 120, 18));

  const box = linkTextLineBox(surface, textLayer, {
    x0: 0.08, y0: 0.215, x1: 0.105, y1: 0.235,
  });

  expect(box.x0).toBeCloseTo(87 / 600, 4);
  expect(box.x1).toBeCloseTo(269 / 600, 4);
  expect(box.y0).toBeCloseTo(196 / 800, 4);
  expect(box.y1).toBeCloseTo(218 / 800, 4);
});
