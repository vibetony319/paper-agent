import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, expect, it } from 'vitest';

import type { ModelProfile } from '../api/types';
import { server } from '../test/server';
import { useModelProfiles } from './useModelProfiles';

const modelProfile = (overrides: Partial<ModelProfile> = {}): ModelProfile => ({
  id: 'profile-a',
  display_name: '本地 Qwen',
  base_url: 'http://127.0.0.1:8001/v1',
  model_name: 'qwen3',
  enabled: true,
  is_default: false,
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

const useProfiles = (profiles: ModelProfile[]) => {
  server.use(http.get('/api/model-profiles', () => HttpResponse.json(profiles)));
};

afterEach(cleanup);

beforeEach(() => {
  window.localStorage.clear();
});

it('falls back from stale paper preference to the enabled default profile', async () => {
  window.localStorage.setItem('paper-agent:selected-model:paper-a', 'deleted-profile');
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true, enabled: true }),
    modelProfile({ id: 'deepseek', is_default: false, enabled: true }),
  ]);

  const { result } = renderHook(() => useModelProfiles('paper-a'));

  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));
});

it('prefers a valid stored paper preference over the default profile', async () => {
  window.localStorage.setItem('paper-agent:selected-model:paper-a', 'deepseek');
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true, enabled: true }),
    modelProfile({ id: 'deepseek', is_default: false, enabled: true }),
  ]);

  const { result } = renderHook(() => useModelProfiles('paper-a'));

  await waitFor(() => expect(result.current.selectedProfileId).toBe('deepseek'));
  expect(result.current.selectedProfile?.id).toBe('deepseek');
});

it('ignores a stored preference that points to a disabled profile', async () => {
  window.localStorage.setItem('paper-agent:selected-model:paper-a', 'deepseek');
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true, enabled: true }),
    modelProfile({ id: 'deepseek', is_default: false, enabled: false }),
  ]);

  const { result } = renderHook(() => useModelProfiles('paper-a'));

  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));
});

it('falls back to the first enabled profile when nothing is default', async () => {
  useProfiles([
    modelProfile({ id: 'disabled-one', enabled: false }),
    modelProfile({ id: 'enabled-one', enabled: true }),
    modelProfile({ id: 'enabled-two', enabled: true }),
  ]);

  const { result } = renderHook(() => useModelProfiles('paper-a'));

  await waitFor(() => expect(result.current.selectedProfileId).toBe('enabled-one'));
});

it('returns null selection and no enabled options when every profile is disabled', async () => {
  useProfiles([modelProfile({ id: 'disabled-one', enabled: false })]);

  const { result } = renderHook(() => useModelProfiles('paper-a'));

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.selectedProfileId).toBeNull();
  expect(result.current.selectedProfile).toBeNull();
});

it('persists the user selection for the current paper', async () => {
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true }),
    modelProfile({ id: 'deepseek' }),
  ]);

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));

  act(() => result.current.selectProfile('deepseek'));

  expect(result.current.selectedProfileId).toBe('deepseek');
  expect(window.localStorage.getItem('paper-agent:selected-model:paper-a')).toBe('deepseek');
});

it('re-reads the preference when the active paper changes', async () => {
  window.localStorage.setItem('paper-agent:selected-model:paper-b', 'deepseek');
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true }),
    modelProfile({ id: 'deepseek' }),
  ]);

  const { result, rerender } = renderHook(
    ({ paperId }) => useModelProfiles(paperId),
    { initialProps: { paperId: 'paper-a' } },
  );
  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));

  rerender({ paperId: 'paper-b' });

  await waitFor(() => expect(result.current.selectedProfileId).toBe('deepseek'));
});

it('reports a chinese error when the profile list fails to load', async () => {
  server.use(http.get('/api/model-profiles', () => HttpResponse.json(
    { detail: '模型档案服务不可用。' },
    { status: 503 },
  )));

  const { result } = renderHook(() => useModelProfiles('paper-a'));

  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.error).toBe('模型列表加载失败。');
  expect(result.current.selectedProfileId).toBeNull();
});

it('creates a profile and appends it to the list', async () => {
  useProfiles([modelProfile({ id: 'qwen', is_default: true })]);
  const created = modelProfile({ id: 'deepseek', display_name: 'DeepSeek' });
  server.use(http.post('/api/model-profiles', () => HttpResponse.json(created, { status: 201 })));

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.profiles).toHaveLength(1));

  await act(async () => {
    await result.current.createProfile({
      display_name: 'DeepSeek',
      base_url: 'http://127.0.0.1:8002/v1',
      model_name: 'deepseek-v3',
      enabled: true,
      is_default: false,
    });
  });

  expect(result.current.profiles.map((profile) => profile.id)).toEqual(['qwen', 'deepseek']);
});

it('moves the default flag to a newly default profile', async () => {
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true }),
    modelProfile({ id: 'deepseek' }),
  ]);
  const updated = modelProfile({ id: 'deepseek', is_default: true, revision: 4 });
  server.use(http.patch('/api/model-profiles/deepseek', () => HttpResponse.json(updated)));

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));

  await act(async () => {
    await result.current.updateProfile('deepseek', 3, { is_default: true });
  });

  expect(result.current.profiles.find((profile) => profile.id === 'qwen')?.is_default).toBe(false);
  expect(result.current.profiles.find((profile) => profile.id === 'deepseek')?.is_default).toBe(true);
});

it('falls back to the default profile when the selected profile is disabled by an update', async () => {
  window.localStorage.setItem('paper-agent:selected-model:paper-a', 'deepseek');
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true }),
    modelProfile({ id: 'deepseek' }),
  ]);
  const updated = modelProfile({ id: 'deepseek', enabled: false, revision: 4 });
  server.use(http.patch('/api/model-profiles/deepseek', () => HttpResponse.json(updated)));

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.selectedProfileId).toBe('deepseek'));

  await act(async () => {
    await result.current.updateProfile('deepseek', 3, { enabled: false });
  });

  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));
});

it('falls back to the default profile after deleting the selected profile', async () => {
  window.localStorage.setItem('paper-agent:selected-model:paper-a', 'deepseek');
  useProfiles([
    modelProfile({ id: 'qwen', is_default: true }),
    modelProfile({ id: 'deepseek' }),
  ]);
  server.use(http.delete(
    '/api/model-profiles/deepseek',
    () => new HttpResponse(null, { status: 204 }),
  ));

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.selectedProfileId).toBe('deepseek'));

  await act(async () => {
    await result.current.deleteProfile('deepseek', 3);
  });

  expect(result.current.profiles.map((profile) => profile.id)).toEqual(['qwen']);
  await waitFor(() => expect(result.current.selectedProfileId).toBe('qwen'));
});

it('merges capability results after testing a profile', async () => {
  useProfiles([modelProfile({ id: 'qwen', is_default: true })]);
  const tested = modelProfile({
    id: 'qwen',
    is_default: true,
    revision: 4,
    capabilities: {
      basic_chat: true,
      structured_output: true,
      tool_calling: false,
      checked_at: '2026-08-21T09:30:00Z',
    },
  });
  let receivedRevision: string | null = null;
  server.use(http.post('/api/model-profiles/qwen/test', ({ request }) => {
    receivedRevision = request.headers.get('If-Match');
    return HttpResponse.json(tested);
  }));

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.profiles).toHaveLength(1));

  await act(async () => {
    await result.current.testProfile('qwen');
  });

  expect(receivedRevision).toBe('3');
  expect(result.current.profiles[0].capabilities.basic_chat).toBe(true);
  expect(result.current.profiles[0].revision).toBe(4);
});

it('propagates api errors from profile actions to the caller', async () => {
  useProfiles([modelProfile({ id: 'qwen', is_default: true })]);
  server.use(http.patch('/api/model-profiles/qwen', () => HttpResponse.json(
    { code: 'REVISION_CONFLICT', detail: '修订号已过期，请刷新后重试。' },
    { status: 409 },
  )));

  const { result } = renderHook(() => useModelProfiles('paper-a'));
  await waitFor(() => expect(result.current.profiles).toHaveLength(1));

  await expect(
    act(async () => {
      await result.current.updateProfile('qwen', 1, { display_name: '新名称' });
    }),
  ).rejects.toMatchObject({ status: 409, code: 'REVISION_CONFLICT' });
});
