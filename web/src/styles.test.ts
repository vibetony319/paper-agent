import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');
const normalizedStyles = styles.replace(/\s+/g, ' ');

it('keeps focus and active colours on the canonical semantic tokens', () => {
  expect(styles).toMatch(/select:focus-visible,\s*\r?\n?a:focus-visible\s*\{[\s\S]*outline:\s*2px solid var\(--accent\)/);
  expect(styles).not.toMatch(/#174f52|#4b6371|#8f3030|#dfe5e7|#e6ecee|#667985|#e8ecee|background:\s*#eeedff/i);
});

it('keeps the workspace error row and split panes constrained inside the viewport', () => {
  expect(styles).toMatch(/\.workspace-shell__content\s*\{[^}]*grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)[^}]*min-height:\s*0/);
  expect(styles).toMatch(/\.workspace-shell__errors\s*\{[^}]*display:\s*grid/);
  expect(styles).toMatch(/\.resizable-split\s*\{[^}]*min-height:\s*0/);
  expect(styles).toMatch(/\.resizable-split__paper\s*,\s*\.resizable-split__tools\s*\{[^}]*min-height:\s*0/);
  expect(normalizedStyles).toMatch(/\.resizable-split\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*var\(--reader-split,\s*62%\)\)\s*0\.72rem\s*minmax\(0,\s*1fr\)/);
});

it('lays out model settings as a readable responsive form instead of inline browser defaults', () => {
  expect(normalizedStyles).toMatch(/\.model-settings\s*\{[^}]*width:\s*min\(42rem,\s*calc\(100vw\s*-\s*2rem\)\)[^}]*max-height:/);
  expect(normalizedStyles).toMatch(/\.model-settings__field\s*\{[^}]*display:\s*grid[^}]*gap:/);
  expect(normalizedStyles).toMatch(/\.model-settings__field\s+input\s*\{[^}]*box-sizing:\s*border-box[^}]*width:\s*100%/);
  expect(normalizedStyles).toMatch(/\.model-settings__form-actions\s*\{[^}]*display:\s*flex/);
});

it('keeps the compact workspace topbar in three short mobile rows', () => {
  expect(normalizedStyles).toMatch(/@media\s*\(max-width:\s*43\.75rem\)[\s\S]*\.workspace-topbar\s*\{[^}]*display:\s*grid[^}]*grid-template-areas:\s*['\"]back model actions['\"]\s*['\"]title title title['\"]\s*['\"]summary summary summary['\"]/);
});
