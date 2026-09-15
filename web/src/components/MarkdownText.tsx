import type { ReactNode } from 'react';

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
  | { kind: 'ordered'; items: string[] };

const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const INLINE = /(\[\[[^\[\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g;

/** Split the model's Markdown into the small set of blocks the reader renders. */
function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length > 0) {
      blocks.push({ kind: 'paragraph', text: paragraph.join(' ') });
      paragraph = [];
    }
  };

  for (const line of text.split('\n')) {
    const heading = HEADING.exec(line);
    if (heading !== null) {
      flush();
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() });
      continue;
    }
    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (bullet !== null || ordered !== null) {
      flush();
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
      flush();
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
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
    if (token.startsWith('[[')) {
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
        return <p key={index}>{inlineNodes(block.text, byId, onSelectCitation)}</p>;
      })}
    </>
  );
}
