import type { ContextUsage } from '../api/types';

const WARNING_PERCENT = 75;
const DANGER_PERCENT = 90;

export type ContextUsageLevel = 'ok' | 'warning' | 'danger' | 'unlimited';

const LEVEL_HINT: Record<ContextUsageLevel, string> = {
  ok: '低于自动压缩阈值。',
  warning: '已达到自动压缩阈值（75%），较早的对话会被折叠成摘要。',
  danger: '已接近硬截断阈值（90%），最早的对话可能被直接丢弃。',
  unlimited: '当前模型未设置上下文长度。',
};

export function contextUsageLevel(usage: ContextUsage): ContextUsageLevel {
  if (usage.effective_limit === null || usage.percent === null) {
    return 'unlimited';
  }
  if (usage.percent >= DANGER_PERCENT) return 'danger';
  if (usage.percent >= WARNING_PERCENT) return 'warning';
  return 'ok';
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 10_000) return `${Math.round(tokens / 1000)}k`;
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
  return String(tokens);
}

export interface ContextUsageBadgeProps {
  usage: ContextUsage;
}

export function ContextUsageBadge({ usage }: ContextUsageBadgeProps) {
  const level = contextUsageLevel(usage);
  const percentText = usage.percent === null ? '' : ` · ${Math.round(usage.percent)}%`;
  const text = usage.effective_limit === null
    ? `上下文 ${formatTokenCount(usage.used_tokens)}（未设上限）`
    : `上下文 ${formatTokenCount(usage.used_tokens)} / ${formatTokenCount(usage.effective_limit)}${percentText}`;
  return (
    <p
      className={`context-usage context-usage--${level}`}
      title={`估算下一次请求的上下文占用（系统提示、最近对话与工具定义，与自动压缩同一估算口径）。${LEVEL_HINT[level]}`}
    >
      <i className="context-usage__dot" aria-hidden="true" />
      {text}
    </p>
  );
}
