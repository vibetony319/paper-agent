import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { ContextUsage } from '../api/types';
import {
  ContextUsageBadge,
  contextUsageLevel,
  formatTokenCount,
} from './ContextUsageBadge';

function usage(overrides: Partial<ContextUsage> = {}): ContextUsage {
  return {
    used_tokens: 8200,
    context_length: 131072,
    effective_limit: 122880,
    compaction_threshold: 92160,
    percent: 6.7,
    ...overrides,
  };
}

afterEach(cleanup);

describe('formatTokenCount', () => {
  it('keeps small counts as plain numbers', () => {
    expect(formatTokenCount(512)).toBe('512');
  });

  it('abbreviates thousands with one decimal', () => {
    expect(formatTokenCount(8200)).toBe('8.2k');
  });

  it('rounds large counts to whole thousands', () => {
    expect(formatTokenCount(131072)).toBe('131k');
  });
});

describe('contextUsageLevel', () => {
  it('marks profiles without a context limit as unlimited', () => {
    expect(contextUsageLevel(usage({ effective_limit: null, percent: null }))).toBe('unlimited');
  });

  it('splits ok, warning and danger around the compaction thresholds', () => {
    expect(contextUsageLevel(usage({ percent: 6.7 }))).toBe('ok');
    expect(contextUsageLevel(usage({ percent: 75 }))).toBe('warning');
    expect(contextUsageLevel(usage({ percent: 90 }))).toBe('danger');
  });
});

describe('ContextUsageBadge', () => {
  it('shows the used/limit ratio with percent', () => {
    render(<ContextUsageBadge usage={usage()} />);
    expect(screen.getByText('上下文 8.2k / 123k · 7%')).toBeVisible();
  });

  it('falls back to an unbounded label when the profile has no context length', () => {
    render(<ContextUsageBadge usage={usage({ context_length: null, effective_limit: null, percent: null })} />);
    expect(screen.getByText('上下文 8.2k（未设上限）')).toBeVisible();
  });

  it('colors the badge by occupancy level', () => {
    const { rerender } = render(<ContextUsageBadge usage={usage({ percent: 6.7 })} />);
    expect(screen.getByText(/上下文/)).toHaveClass('context-usage--ok');

    rerender(<ContextUsageBadge usage={usage({ used_tokens: 95000, percent: 77.3 })} />);
    expect(screen.getByText('上下文 95k / 123k · 77%')).toHaveClass('context-usage--warning');

    rerender(<ContextUsageBadge usage={usage({ used_tokens: 115000, percent: 93.6 })} />);
    expect(screen.getByText('上下文 115k / 123k · 94%')).toHaveClass('context-usage--danger');
  });
});
