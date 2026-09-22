import type { ReactNode } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';

import type { Citation } from '../api/types';

export interface MarkdownTextProps {
  text: string;
  citations?: Citation[];
  onSelectCitation?: (citation: Citation) => void;
}

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'bullet'; items: string[] }
  | { kind: 'ordered'; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'math'; latex: string };

const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.+\|\s*$/;
const MATH_FENCE = /^\s*\$\$\s*$/;
const SEPARATOR_CELL = /^:?-{1,}:?$/;
// Display math ($$...$$) must be tried before inline math ($...$); inline
// math requires a non-space edge so prose like "cost $5 and $6" stays text.
const INLINE =
  /(\$\$[\s\S]+?\$\$|\$[^\s$](?:[^$\n]*[^\s$])?\$|\[\[[^\[\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g;

/** Split one Markdown table row into trimmed cells, unescaping `\|`. */
function splitRow(line: string): string[] {
  let body = line.trim();
  if (body.startsWith('|')) body = body.slice(1);
  if (body.endsWith('|') && !body.endsWith('\\|')) body = body.slice(0, -1);
  const cells: string[] = [];
  let current = '';
  for (let index = 0; index < body.length; index += 1) {
    const character = body[index];
    if (character === '\\' && body[index + 1] === '|') {
      current += '|';
      index += 1;
    } else if (character === '|') {
      cells.push(current.trim());
      current = '';
    } else {
      current += character;
    }
  }
  cells.push(current.trim());
  return cells;
}

function isSeparatorRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((cell) => SEPARATOR_CELL.test(cell));
}

/** Split the model's Markdown into the small set of blocks the reader renders. */
function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let table: string[] = [];
  let math: string[] | null = null;
  const flushParagraph = () => {
    if (paragraph.length > 0) {
      blocks.push({ kind: 'paragraph', text: paragraph.join(' ') });
      paragraph = [];
    }
  };
  const flushTable = () => {
    if (table.length === 0) return;
    const candidate = table;
    table = [];
    if (
      candidate.length >= 2 &&
      isSeparatorRow(splitRow(candidate[1]))
    ) {
      blocks.push({
        kind: 'table',
        header: splitRow(candidate[0]),
        rows: candidate.slice(2).map(splitRow),
      });
    } else {
      // An unterminated or separator-less pipe block (typical while the
      // answer is still streaming) renders as plain paragraphs and settles
      // into a table once the separator row arrives.
      paragraph.push(...candidate.map((line) => line.trim()));
    }
  };

  for (const line of text.split('\n')) {
    if (math !== null) {
      if (MATH_FENCE.test(line)) {
        blocks.push({ kind: 'math', latex: math.join('\n') });
        math = null;
      } else {
        math.push(line);
      }
      continue;
    }
    if (MATH_FENCE.test(line)) {
      flushParagraph();
      flushTable();
      math = [];
      continue;
    }
    if (TABLE_ROW.test(line)) {
      flushParagraph();
      table.push(line);
      continue;
    }
    flushTable();
    const heading = HEADING.exec(line);
    if (heading !== null) {
      flushParagraph();
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() });
      continue;
    }
    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (bullet !== null || ordered !== null) {
      flushParagraph();
      const kind = bullet !== null ? 'bullet' : 'ordered';
      const item = (bullet?.[1] ?? ordered?.[1] ?? '').trim();
      const previous = blocks[blocks.length - 1];
      if (previous !== undefined && previous.kind === kind) {
        previous.items.push(item);
      } else {
        blocks.push({ kind, items: [item] });
      }
      continue;
    }
    if (line.trim() === '') {
      flushParagraph();
      continue;
    }
    paragraph.push(line.trim());
  }
  if (math !== null) {
    // A display-math fence that never closed (mid-stream) shows its raw lines.
    paragraph.push('$$', ...math);
  }
  flushTable();
  flushParagraph();
  return blocks;
}

function mathNode(latex: string, displayMode: boolean, key: string): ReactNode {
  try {
    const html = katex.renderToString(latex, {
      displayMode,
      throwOnError: false,
      strict: 'ignore',
    });
    return (
      <span
        key={key}
        className={displayMode ? 'markdown-math markdown-math--display' : 'markdown-math'}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  } catch {
    return latex;
  }
}

function inlineNodes(
  text: string,
  citations: Map<string, Citation>,
  onSelectCitation?: (citation: Citation) => void,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0;
    if (index > lastIndex) {
      nodes.push(text.slice(lastIndex, index));
    }
    const token = match[0];
    key += 1;
    if (token.startsWith('$$')) {
      nodes.push(mathNode(token.slice(2, -2), true, `math-${key}`));
    } else if (token.startsWith('$')) {
      nodes.push(mathNode(token.slice(1, -1), false, `math-${key}`));
    } else if (token.startsWith('[[')) {
      const citation = citations.get(token.slice(2, -2).trim());
      nodes.push(
        citation === undefined ? (
          token
        ) : (
          <button
            key={`citation-${key}`}
            type="button"
            className="markdown-citation"
            onClick={() => onSelectCitation?.(citation)}
          >
            第 {citation.page_number} 页
          </button>
        ),
      );
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={`strong-${key}`}>{token.slice(2, -2)}</strong>);
    } else {
      nodes.push(<code key={`code-${key}`}>{token.slice(1, -1)}</code>);
    }
    lastIndex = index + token.length;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

/** A paragraph that is entirely one display formula renders as math. */
function isDisplayMathParagraph(text: string): boolean {
  return text.startsWith('$$') && text.endsWith('$$') && text.length > 4;
}

/** Render the paper agent's Markdown answer without pulling in a parser library. */
export function MarkdownText({ text, citations = [], onSelectCitation }: MarkdownTextProps) {
  const byId = new Map(citations.map((citation) => [citation.id, citation]));
  return (
    <>
      {toBlocks(text).map((block, index) => {
        if (block.kind === 'bullet' || block.kind === 'ordered') {
          const List = block.kind === 'bullet' ? 'ul' : 'ol';
          return (
            <List className="markdown-list" key={index}>
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{inlineNodes(item, byId, onSelectCitation)}</li>
              ))}
            </List>
          );
        }
        if (block.kind === 'heading') {
          const Heading = `h${Math.min(block.level + 2, 5)}` as 'h3';
          return <Heading key={index}>{inlineNodes(block.text, byId, onSelectCitation)}</Heading>;
        }
        if (block.kind === 'table') {
          return (
            <div className="markdown-table-scroll" key={index}>
              <table className="markdown-table">
                <thead>
                  <tr>
                    {block.header.map((cell, cellIndex) => (
                      <th key={cellIndex}>{inlineNodes(cell, byId, onSelectCitation)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex}>{inlineNodes(cell, byId, onSelectCitation)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        if (block.kind === 'math') {
          return <div className="markdown-math-block" key={index}>{mathNode(block.latex, true, `math-${index}`)}</div>;
        }
        if (isDisplayMathParagraph(block.text)) {
          return (
            <div className="markdown-math-block" key={index}>
              {mathNode(block.text.slice(2, -2), true, `math-${index}`)}
            </div>
          );
        }
        return <p key={index}>{inlineNodes(block.text, byId, onSelectCitation)}</p>;
      })}
    </>
  );
}
