import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import type { ModelProfile } from '../api/types';
import { ModelSettingsDialog } from './ModelSettingsDialog';
import type { ModelSettingsDialogProps } from './ModelSettingsDialog';

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

type ActionOverrides = Partial<Pick<
  ModelSettingsDialogProps,
  'onRefresh' | 'onCreate' | 'onUpdate' | 'onDelete' | 'onTest'
>>;

function renderDialog(profiles: ModelProfile[], overrides: ActionOverrides = {}) {
  const props: ModelSettingsDialogProps = {
    open: true,
    profiles,
    onClose: vi.fn(),
    onRefresh: vi.fn().mockResolvedValue(undefined),
    onCreate: vi.fn().mockResolvedValue(modelProfile()),
    onUpdate: vi.fn().mockResolvedValue(modelProfile()),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onTest: vi.fn().mockResolvedValue(modelProfile()),
    ...overrides,
  };
  render(<ModelSettingsDialog {...props} />);
  return props;
}

afterEach(cleanup);

it('lists profiles with chinese capability states and the default badge', () => {
  renderDialog([
    modelProfile(),
    modelProfile({
      id: 'deepseek',
      display_name: 'DeepSeek 远程',
      is_default: false,
      capabilities: {
        basic_chat: true,
        structured_output: true,
        tool_calling: false,
        checked_at: '2026-08-21T09:30:00Z',
      },
    }),
  ]);

  expect(screen.getByRole('dialog', { name: '模型设置' })).toBeVisible();
  const qwen = within(screen.getByRole('listitem', { name: '本地 Qwen' }));
  expect(qwen.getByText('默认')).toBeVisible();
  expect(qwen.getByText('基础对话：未测试')).toBeVisible();

  const deepseek = within(screen.getByRole('listitem', { name: 'DeepSeek 远程' }));
  expect(deepseek.getByText('基础对话：检测通过')).toBeVisible();
  expect(deepseek.getByText('结构化输出：检测通过')).toBeVisible();
  expect(deepseek.getByText('工具调用：检测未通过（可能是接口不兼容，请重新测试或检查服务配置）')).toBeVisible();
});

it('creates a profile from the chinese form fields', async () => {
  const user = userEvent.setup();
  const props = renderDialog([]);

  await user.click(screen.getByRole('button', { name: '新增模型档案' }));
  await user.type(screen.getByLabelText('配置名称'), '本地 Qwen');
  await user.type(screen.getByLabelText('服务地址'), 'http://127.0.0.1:8001/v1');
  await user.type(screen.getByLabelText('模型名称'), 'qwen3');
  await user.type(screen.getByLabelText('API 密钥（可选）'), 'sk-secret');
  await user.type(screen.getByLabelText('上下文长度（tokens，可选）'), '131072');
  await user.type(screen.getByLabelText('最大输出（tokens，可选）'), '8192');
  await user.click(screen.getByLabelText('设为默认'));
  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(props.onCreate).toHaveBeenCalledWith({
    display_name: '本地 Qwen',
    base_url: 'http://127.0.0.1:8001/v1',
    model_name: 'qwen3',
    api_key: 'sk-secret',
    enabled: true,
    is_default: true,
    context_length: 131072,
    max_output_tokens: 8192,
  });
});

it('keeps the existing api key when the edit form leaves the key field blank', async () => {
  const user = userEvent.setup();
  const profile = modelProfile({ has_api_key: true, api_key_mask: 'sk-···cret' });
  const props = renderDialog([profile]);

  await user.click(screen.getByRole('button', { name: '编辑' }));
  await user.clear(screen.getByLabelText('配置名称'));
  await user.type(screen.getByLabelText('配置名称'), '本地 Qwen 二区');
  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(props.onUpdate).toHaveBeenCalledWith('qwen', 3, {
    display_name: '本地 Qwen 二区',
    base_url: 'http://127.0.0.1:8001/v1',
    model_name: 'qwen3',
    is_default: true,
    context_length: null,
    max_output_tokens: null,
  });
});

it('clears the api key only after an explicit clear request', async () => {
  const user = userEvent.setup();
  const profile = modelProfile({ has_api_key: true, api_key_mask: 'sk-···cret' });
  const props = renderDialog([profile]);

  await user.click(screen.getByRole('button', { name: '编辑' }));
  await user.click(screen.getByLabelText('清除 API 密钥'));
  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(props.onUpdate).toHaveBeenCalledWith('qwen', 3, {
    display_name: '本地 Qwen',
    base_url: 'http://127.0.0.1:8001/v1',
    model_name: 'qwen3',
    is_default: true,
    clear_api_key: true,
    context_length: null,
    max_output_tokens: null,
  });
});

it('shows the three chinese capability results after a connection test', async () => {
  const user = userEvent.setup();
  const onTest = vi.fn().mockResolvedValue(modelProfile({
    revision: 4,
    capabilities: {
      basic_chat: true,
      structured_output: true,
      tool_calling: false,
      checked_at: '2026-08-21T09:30:00Z',
    },
  }));
  renderDialog([modelProfile()], { onTest });

  await user.click(screen.getByRole('button', { name: '测试能力' }));

  expect(onTest).toHaveBeenCalledWith('qwen');
  expect(await screen.findByText('基础对话：检测通过')).toBeVisible();
  expect(screen.getByText('结构化输出：检测通过')).toBeVisible();
  expect(screen.getByText('工具调用：检测未通过（可能是接口不兼容，请重新测试或检查服务配置）')).toBeVisible();
});

it('refreshes the profile list after a revision conflict', async () => {
  const user = userEvent.setup();
  const onUpdate = vi.fn().mockRejectedValue(
    new ApiError(409, '修订号已过期，请刷新后重试。', 'REVISION_CONFLICT'),
  );
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  renderDialog([modelProfile()], { onUpdate, onRefresh });

  await user.click(screen.getByRole('button', { name: '编辑' }));
  await user.click(screen.getByRole('button', { name: '保存' }));

  await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
  expect(await screen.findByRole('alert')).toHaveTextContent('已刷新');
});

it('asks for a second confirmation before deleting a profile', async () => {
  const user = userEvent.setup();
  const props = renderDialog([modelProfile()]);

  await user.click(screen.getByRole('button', { name: '删除' }));
  expect(props.onDelete).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: '取消' }));
  expect(props.onDelete).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: '删除' }));
  await user.click(screen.getByRole('button', { name: '确认删除' }));
  expect(props.onDelete).toHaveBeenCalledWith('qwen', 3);
});

it('disables edit and delete for the read-only environment profile but keeps testing', () => {
  renderDialog([
    modelProfile({ id: 'env', display_name: '环境变量模型', read_only: true }),
  ]);

  const item = within(screen.getByRole('listitem', { name: '环境变量模型' }));
  expect(item.getByText('只读')).toBeVisible();
  expect(item.getByRole('button', { name: '编辑' })).toBeDisabled();
  expect(item.getByRole('button', { name: '删除' })).toBeDisabled();
  expect(item.getByRole('button', { name: '测试能力' })).toBeEnabled();
});

it('returns focus to the settings trigger after closing', async () => {
  const user = userEvent.setup();

  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>模型设置</button>
        <ModelSettingsDialog
          open={open}
          profiles={[]}
          onClose={() => setOpen(false)}
          onRefresh={vi.fn().mockResolvedValue(undefined)}
          onCreate={vi.fn()}
          onUpdate={vi.fn()}
          onDelete={vi.fn()}
          onTest={vi.fn()}
        />
      </>
    );
  }

  render(<Harness />);

  await user.click(screen.getByRole('button', { name: '模型设置' }));
  expect(await screen.findByRole('dialog', { name: '模型设置' })).toBeVisible();

  await user.click(screen.getByRole('button', { name: '关闭' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name: '模型设置' })).toHaveFocus();
});

it('backfills the token limits when editing and keeps them on save', async () => {
  const user = userEvent.setup();
  const profile = modelProfile({
    context_length: 131072,
    max_output_tokens: 8192,
  });
  const props = renderDialog([profile]);

  await user.click(screen.getByRole('button', { name: '编辑' }));
  expect(screen.getByLabelText('上下文长度（tokens，可选）')).toHaveValue('131072');
  expect(screen.getByLabelText('最大输出（tokens，可选）')).toHaveValue('8192');

  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(props.onUpdate).toHaveBeenCalledWith('qwen', 3, {
    display_name: '本地 Qwen',
    base_url: 'http://127.0.0.1:8001/v1',
    model_name: 'qwen3',
    is_default: true,
    context_length: 131072,
    max_output_tokens: 8192,
  });
});

it('clears the token limits when the edit form leaves them empty', async () => {
  const user = userEvent.setup();
  const profile = modelProfile({
    context_length: 131072,
    max_output_tokens: 8192,
  });
  const props = renderDialog([profile]);

  await user.click(screen.getByRole('button', { name: '编辑' }));
  await user.clear(screen.getByLabelText('上下文长度（tokens，可选）'));
  await user.clear(screen.getByLabelText('最大输出（tokens，可选）'));
  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(props.onUpdate).toHaveBeenCalledWith('qwen', 3, {
    display_name: '本地 Qwen',
    base_url: 'http://127.0.0.1:8001/v1',
    model_name: 'qwen3',
    is_default: true,
    context_length: null,
    max_output_tokens: null,
  });
});

it('rejects non-positive token limits before calling the API', async () => {
  const user = userEvent.setup();
  const props = renderDialog([]);

  await user.click(screen.getByRole('button', { name: '新增模型档案' }));
  await user.type(screen.getByLabelText('配置名称'), '本地 Qwen');
  await user.type(screen.getByLabelText('服务地址'), 'http://127.0.0.1:8001/v1');
  await user.type(screen.getByLabelText('模型名称'), 'qwen3');
  await user.type(screen.getByLabelText('上下文长度（tokens，可选）'), '128k');
  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    '上下文长度与最大输出必须是正整数（tokens）。',
  );
  expect(props.onCreate).not.toHaveBeenCalled();
});

it('rejects an output cap at or above the context length', async () => {
  const user = userEvent.setup();
  const props = renderDialog([]);

  await user.click(screen.getByRole('button', { name: '新增模型档案' }));
  await user.type(screen.getByLabelText('配置名称'), '本地 Qwen');
  await user.type(screen.getByLabelText('服务地址'), 'http://127.0.0.1:8001/v1');
  await user.type(screen.getByLabelText('模型名称'), 'qwen3');
  await user.type(screen.getByLabelText('上下文长度（tokens，可选）'), '8192');
  await user.type(screen.getByLabelText('最大输出（tokens，可选）'), '8192');
  await user.click(screen.getByRole('button', { name: '保存' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    '最大输出 tokens 必须小于上下文长度。',
  );
  expect(props.onCreate).not.toHaveBeenCalled();
});

it('shows the configured token limits in the profile list', () => {
  renderDialog([
    modelProfile({ context_length: 131072, max_output_tokens: 8192 }),
  ]);

  expect(
    screen.getByText('qwen3 · http://127.0.0.1:8001/v1 · 上下文 131072 · 输出 8192'),
  ).toBeVisible();
});
