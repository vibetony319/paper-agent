import { useEffect, useRef, useState, type ReactNode } from 'react';

const STORAGE_KEY = 'paper-agent:reader-split';
const DEFAULT_PERCENTAGE = 62;
const MIN_PERCENTAGE = 45;
const MAX_PERCENTAGE = 78;

function clamp(value: number): number {
  return Math.min(MAX_PERCENTAGE, Math.max(MIN_PERCENTAGE, value));
}

function initialPercentage(): number {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === null) return DEFAULT_PERCENTAGE;
  const value = Number(stored);
  return Number.isFinite(value) ? clamp(value) : DEFAULT_PERCENTAGE;
}

export interface ResizableSplitProps {
  paper: ReactNode;
  tools: ReactNode;
}

export function ResizableSplit({ paper, tools }: ResizableSplitProps) {
  const [percentage, setPercentage] = useState(initialPercentage);
  const [compact, setCompact] = useState(() => window.innerWidth <= 880);
  const [visiblePane, setVisiblePane] = useState<'paper' | 'tools'>('paper');
  const rootRef = useRef<HTMLDivElement>(null);
  const activePointerId = useRef<number | null>(null);

  const updatePercentage = (nextValue: number) => {
    const next = clamp(nextValue);
    setPercentage(next);
    localStorage.setItem(STORAGE_KEY, String(next));
  };

  useEffect(() => {
    const onResize = () => setCompact(window.innerWidth <= 880);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const movePointer = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (activePointerId.current !== event.pointerId) return;
    const bounds = rootRef.current?.getBoundingClientRect();
    if (bounds === undefined || bounds.width <= 0) return;
    updatePercentage(((event.clientX - bounds.left) / bounds.width) * 100);
  };

  const releasePointer = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (activePointerId.current === event.pointerId) {
      activePointerId.current = null;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  };

  return (
    <div
      ref={rootRef}
      className={`resizable-split${compact ? ' resizable-split--compact' : ''}`}
      data-testid="resizable-split"
      style={{ '--reader-split': `${percentage}%` } as React.CSSProperties}
    >
      {compact && (
        <div className="resizable-split__switches" aria-label="阅读区域切换">
          <button type="button" aria-pressed={visiblePane === 'paper'} onClick={() => setVisiblePane('paper')}>论文</button>
          <button type="button" aria-pressed={visiblePane === 'tools'} onClick={() => setVisiblePane('tools')}>工具</button>
        </div>
      )}
      <div className="resizable-split__paper" hidden={compact && visiblePane !== 'paper'}>{paper}</div>
      {!compact && (
        <button
          type="button"
          className="resizable-split__separator"
          role="separator"
          aria-label="调整论文与工具宽度"
          aria-orientation="vertical"
          aria-valuemin={MIN_PERCENTAGE}
          aria-valuemax={MAX_PERCENTAGE}
          aria-valuenow={percentage}
          onPointerDown={(event) => {
            activePointerId.current = event.pointerId;
            event.currentTarget.setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={movePointer}
          onPointerUp={releasePointer}
          onPointerCancel={releasePointer}
          onLostPointerCapture={releasePointer}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') { event.preventDefault(); updatePercentage(percentage - 2); }
            if (event.key === 'ArrowRight') { event.preventDefault(); updatePercentage(percentage + 2); }
          }}
        />
      )}
      <div className="resizable-split__tools" hidden={compact && visiblePane !== 'tools'}>{tools}</div>
    </div>
  );
}
