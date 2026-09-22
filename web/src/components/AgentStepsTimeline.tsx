import type { AgentStreamStep } from '../api/types';

const TOOL_LABELS: Record<string, string> = {
  search_paper: '检索论文内容',
  read_element: '阅读论文元素',
  read_section: '阅读章节',
  list_sections: '查看章节目录',
};

const MAX_ARG_CHARS = 60;

function argumentSummary(arguments_: Record<string, unknown> | undefined): string {
  if (arguments_ === undefined) return '';
  const candidate =
    arguments_.query ?? arguments_.element_id ?? arguments_.section_id;
  if (candidate === undefined || candidate === null) return '';
  const text = String(candidate);
  return text.length > MAX_ARG_CHARS ? `${text.slice(0, MAX_ARG_CHARS)}…` : text;
}

function StepBody({ step }: { step: AgentStreamStep }) {
  switch (step.kind) {
    case 'notes':
      return (
        <p>{step.count > 0 ? `检索到 ${step.count} 条相关笔记` : '检索相关笔记'}</p>
      );
    case 'round':
      return <p>第 {step.round} 轮推理</p>;
    case 'reasoning':
      return (
        <details className="agent-steps__thinking">
          <summary>思考过程</summary>
          <p>{step.text}</p>
        </details>
      );
    case 'tool_call': {
      const label = TOOL_LABELS[step.tool_name] ?? step.tool_name;
      const summary = argumentSummary(step.arguments);
      return <p>{summary === '' ? `调用${label}` : `调用${label}：${summary}`}</p>;
    }
    case 'tool_result':
      return step.error === undefined ? (
        <p>获得 {step.evidence_count ?? 0} 条证据</p>
      ) : (
        <p className="agent-steps__failure">调用失败：{step.error}</p>
      );
    case 'compaction':
      return <p>压缩对话上下文</p>;
    case 'final_answer':
      return <p>正在生成最终回答</p>;
  }
}

export interface AgentStepsTimelineProps {
  steps: AgentStreamStep[];
  /** True while the answer is still being generated: animates the latest step. */
  active?: boolean;
}

export function AgentStepsTimeline({ steps, active = false }: AgentStepsTimelineProps) {
  if (steps.length === 0) return null;
  return (
    <ol className="agent-steps" aria-label="执行过程">
      {steps.map((step, index) => {
        const running = active && index === steps.length - 1;
        return (
          <li key={index} className="agent-steps__item" data-step-kind={step.kind}>
            <span className="agent-steps__marker" aria-hidden="true">
              {running ? (
                <span className="chat-typing">
                  <i />
                  <i />
                  <i />
                </span>
              ) : null}
            </span>
            <div className="agent-steps__body">
              <StepBody step={step} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
