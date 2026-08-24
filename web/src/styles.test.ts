import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');

it('keeps focus and active colours on the canonical semantic tokens', () => {
  expect(styles).toMatch(/select:focus-visible,\s*\r?\n?a:focus-visible\s*\{[\s\S]*outline:\s*2px solid var\(--accent\)/);
  expect(styles).not.toMatch(/#174f52|#4b6371|#8f3030|#dfe5e7|#e6ecee|#667985|#e8ecee|background:\s*#eeedff/i);
});

it('keeps the workspace error row and split panes constrained inside the viewport', () => {
  expect(styles).toMatch(/\.workspace-shell__content\s*\{[^}]*grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)[^}]*min-height:\s*0/);
  expect(styles).toMatch(/\.workspace-shell__errors\s*\{[^}]*display:\s*grid/);
  expect(styles).toMatch(/\.resizable-split\s*\{[^}]*min-height:\s*0/);
  expect(styles).toMatch(/\.resizable-split__paper\s*,\s*\.resizable-split__tools\s*\{[^}]*min-height:\s*0/);
});
