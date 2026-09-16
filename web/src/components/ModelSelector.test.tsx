import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import type { ModelProfile } from '../api/types';
import { ModelSelector } from './ModelSelector';

const modelProfile = (overrides: Partial<ModelProfile> = {}): ModelProfile => ({
  id: 'qwen',
  display_name: '本地 Qwen',
  base_url: 'http://127.0.0.1:8001/v1',
  model_name: 'qwen3',
  enabled: true,
  is_default: true,
  revision: 3,
  has_api_key: false,
  api_key_mask: null,
  context_length: null,
  max_output_tokens: null,
  capabilities: {
    basic_chat: false,
    structured_output: false,
    tool_calling: false,
    checked_at: null,
  },
  read_only: false,
  ...overrides,
});

const profiles = [
  modelProfile({ id: 'qwen', display_name: '本地 Qwen' }),
  modelProfile({ id: 'deepseek', display_name: 'DeepSeek 远程', is_default: false }),
];

afterEach(cleanup);

it('switches the selected model without clearing the current conversation', async () => {
  const onChange = vi.fn();
  render(<ModelSelector profiles={profiles} value="qwen" onChange={onChange} />);

  await userEvent.selectOptions(screen.getByLabelText('当前模型'), 'deepseek');

  expect(onChange).toHaveBeenCalledWith('deepseek');
});

it('only offers enabled profiles as options', () => {
  render(
    <ModelSelector
      profiles={[...profiles, modelProfile({ id: 'off', display_name: '停用模型', enabled: false })]}
      value="qwen"
      onChange={vi.fn()}
    />,
  );

  const options = screen.getAllByRole('option').map((option) => option.textContent);
  expect(options).toEqual(['本地 Qwen', 'DeepSeek 远程']);
});

it('shows a disabled chinese empty state when no model is available', () => {
  render(<ModelSelector profiles={[]} value={null} onChange={vi.fn()} />);

  const select = screen.getByLabelText('当前模型');
  expect(select).toBeDisabled();
  expect(screen.getByRole('option')).toHaveTextContent('无可用模型');
});
