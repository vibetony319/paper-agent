import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');

it('keeps focus and active colours on the canonical semantic tokens', () => {
  expect(styles).toMatch(/select:focus-visible,\s*\r?\n?a:focus-visible\s*\{[\s\S]*outline:\s*2px solid var\(--accent\)/);
  expect(styles).not.toMatch(/#174f52|#4b6371|#8f3030|#dfe5e7|#e6ecee|#667985|#e8ecee|background:\s*#eeedff/i);
});
