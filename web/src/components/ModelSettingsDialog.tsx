import { useEffect, useId, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import type {
  ModelProfile,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
} from '../api/types';

export interface ModelSettingsDialogProps {
  open: boolean;
  profiles: ModelProfile[];
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onCreate: (input: ModelProfileCreateInput) => Promise<ModelProfile>;
  onUpdate: (
    id: string,
    revision: number,
    input: ModelProfileUpdateInput,
  ) => Promise<ModelProfile>;
  onDelete: (id: string, revision: number) => Promise<void>;
  onTest: (id: string) => Promise<ModelProfile>;
}

type DialogView = 'list' | 'create' | 'edit';

type CapabilityKey = 'basic_chat' | 'structured_output' | 'tool_calling';

const CAPABILITY_LABELS: Array<{ key: CapabilityKey; label: string }> = [
  { key: 'basic_chat', label: '基础对话' },
  { key: 'structured_output', label: '结构化输出' },
  { key: 'tool_calling', label: '工具调用' },
];

const ACTION_FAILED_MESSAGE = '操作失败，请重试。';
const REVISION_CONFLICT_MESSAGE = '档案数据已被其他修改更新，列表已刷新，请重试。';
const REQUIRED_FIELDS_MESSAGE = '请填写配置名称、服务地址和模型名称。';

function capabilityText(capabilities: ModelProfile['capabilities'], key: CapabilityKey): string {
  if (capabilities.checked_at === null) {
    return '未测试';
  }
  return capabilities[key] ? '检测通过' : '检测未通过（可能是接口不兼容，请重新测试或检查服务配置）';
}

export function ModelSettingsDialog({
  open,
  profiles,
  onClose,
  onRefresh,
  onCreate,
  onUpdate,
  onDelete,
  onTest,
}: ModelSettingsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const fieldId = useId();

  const [view, setView] = useState<DialogView>('list');
  const [editingProfile, setEditingProfile] = useState<ModelProfile | null>(null);
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [testedCapabilities, setTestedCapabilities] = useState<
    Record<string, ModelProfile['capabilities']>
  >({});

  const [displayName, setDisplayName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [modelName, setModelName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [isDefault, setIsDefault] = useState(false);
  const [clearApiKey, setClearApiKey] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const dialog = dialogRef.current;
    if (dialog !== null && !dialog.open) {
      if (typeof dialog.showModal === 'function') {
        dialog.showModal();
      } else {
        dialog.setAttribute('open', '');
      }
    }
    setView('list');
    setEditingProfile(null);
    setConfirmingDeleteId(null);
    setErrorMessage(null);
    setPending(false);
    return () => {
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
  }, [open]);

  if (!open) {
    return null;
  }

  const resetForm = () => {
    setDisplayName('');
    setBaseUrl('');
    setModelName('');
    setApiKey('');
    setIsDefault(false);
    setClearApiKey(false);
  };

  const backToList = () => {
    setView('list');
    setEditingProfile(null);
    setErrorMessage(null);
    setConfirmingDeleteId(null);
  };

  const handleActionError = async (cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 409) {
      backToList();
      await onRefresh();
      setErrorMessage(REVISION_CONFLICT_MESSAGE);
      return;
    }
    setErrorMessage(cause instanceof ApiError ? cause.message : ACTION_FAILED_MESSAGE);
  };

  const startEdit = (profile: ModelProfile) => {
    setEditingProfile(profile);
    setDisplayName(profile.display_name);
    setBaseUrl(profile.base_url);
    setModelName(profile.model_name);
    setApiKey('');
    setIsDefault(profile.is_default);
    setClearApiKey(false);
    setErrorMessage(null);
    setView('edit');
  };

  const startCreate = () => {
    resetForm();
    setErrorMessage(null);
    setView('create');
  };

  const submitForm = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedName = displayName.trim();
    const trimmedBaseUrl = baseUrl.trim();
    const trimmedModelName = modelName.trim();
    if (trimmedName === '' || trimmedBaseUrl === '' || trimmedModelName === '') {
      setErrorMessage(REQUIRED_FIELDS_MESSAGE);
      return;
    }

    setPending(true);
    setErrorMessage(null);
    try {
      if (view === 'create') {
        const input: ModelProfileCreateInput = {
          display_name: trimmedName,
          base_url: trimmedBaseUrl,
          model_name: trimmedModelName,
          enabled: true,
          is_default: isDefault,
        };
        if (apiKey !== '') {
          input.api_key = apiKey;
        }
        await onCreate(input);
      } else if (editingProfile !== null) {
        const input: ModelProfileUpdateInput = {
          display_name: trimmedName,
          base_url: trimmedBaseUrl,
          model_name: trimmedModelName,
          is_default: isDefault,
        };
        if (clearApiKey) {
          input.clear_api_key = true;
        } else if (apiKey !== '') {
          input.api_key = apiKey;
        }
        await onUpdate(editingProfile.id, editingProfile.revision, input);
      }
      backToList();
    } catch (cause) {
      await handleActionError(cause);
    } finally {
      setPending(false);
    }
  };

  const runTest = async (profile: ModelProfile) => {
    setPending(true);
    setErrorMessage(null);
    try {
      const tested = await onTest(profile.id);
      setTestedCapabilities((current) => ({
        ...current,
        [profile.id]: tested.capabilities,
      }));
    } catch (cause) {
      await handleActionError(cause);
    } finally {
      setPending(false);
    }
  };

  const confirmDelete = async (profile: ModelProfile) => {
    setPending(true);
    setErrorMessage(null);
    try {
      await onDelete(profile.id, profile.revision);
      setConfirmingDeleteId(null);
    } catch (cause) {
      await handleActionError(cause);
    } finally {
      setPending(false);
    }
  };

  return (
    <dialog
      ref={dialogRef}
      className="model-settings"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <header className="model-settings__header">
        <h2 id={titleId}>模型设置</h2>
        <button type="button" onClick={onClose}>关闭</button>
      </header>

      {errorMessage !== null && <p role="alert">{errorMessage}</p>}

      {view === 'list' ? (
        <>
          {profiles.length === 0 ? (
            <p>暂无模型档案，请新增一个 vLLM 服务。</p>
          ) : (
            <ul className="model-settings__list">
              {profiles.map((profile) => {
                const capabilities = testedCapabilities[profile.id] ?? profile.capabilities;
                return (
                  <li key={profile.id} aria-label={profile.display_name}>
                    <div className="model-settings__item-header">
                      <strong>{profile.display_name}</strong>
                      {profile.is_default && <span>默认</span>}
                      {profile.read_only && <span>只读</span>}
                      {!profile.enabled && <span>已停用</span>}
                    </div>
                    <p>{profile.model_name} · {profile.base_url}</p>
                    <ul className="model-settings__capabilities">
                      {CAPABILITY_LABELS.map(({ key, label }) => (
                        <li key={key}>{label}：{capabilityText(capabilities, key)}</li>
                      ))}
                    </ul>
                    {confirmingDeleteId === profile.id ? (
                      <div className="model-settings__confirm">
                        <p>确认删除模型档案「{profile.display_name}」？历史记录中的模型快照会保留。</p>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => void confirmDelete(profile)}
                        >
                          确认删除
                        </button>
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => setConfirmingDeleteId(null)}
                        >
                          取消
                        </button>
                      </div>
                    ) : (
                      <div className="model-settings__actions">
                        <button
                          type="button"
                          disabled={pending}
                          onClick={() => void runTest(profile)}
                        >
                          测试能力
                        </button>
                        <button
                          type="button"
                          disabled={pending || profile.read_only}
                          onClick={() => startEdit(profile)}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          disabled={pending || profile.read_only}
                          onClick={() => setConfirmingDeleteId(profile.id)}
                        >
                          删除
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <button type="button" onClick={startCreate}>新增模型档案</button>
        </>
      ) : (
        <form
          className="model-settings__form"
          aria-label={view === 'create' ? '新增模型档案' : '编辑模型档案'}
          onSubmit={(event) => void submitForm(event)}
        >
          <h3>{view === 'create' ? '新增模型档案' : `编辑「${editingProfile?.display_name ?? ''}」`}</h3>
          <div className="model-settings__field">
            <label htmlFor={`${fieldId}-name`}>配置名称</label>
            <input
              id={`${fieldId}-name`}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              required
            />
          </div>
          <div className="model-settings__field">
            <label htmlFor={`${fieldId}-base-url`}>服务地址</label>
            <input
              id={`${fieldId}-base-url`}
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="http://127.0.0.1:8001/v1"
              required
            />
          </div>
          <div className="model-settings__field">
            <label htmlFor={`${fieldId}-model-name`}>模型名称</label>
            <input
              id={`${fieldId}-model-name`}
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              required
            />
          </div>
          <div className="model-settings__field">
            <label htmlFor={`${fieldId}-api-key`}>API 密钥（可选）</label>
            <input
              id={`${fieldId}-api-key`}
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={view === 'edit' ? '留空表示不修改' : undefined}
              disabled={clearApiKey}
              autoComplete="off"
            />
          </div>
          {view === 'edit' && editingProfile?.has_api_key === true && (
            <label className="model-settings__checkbox">
              <input
                type="checkbox"
                checked={clearApiKey}
                onChange={(event) => setClearApiKey(event.target.checked)}
              />
              清除 API 密钥
            </label>
          )}
          <label className="model-settings__checkbox">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(event) => setIsDefault(event.target.checked)}
            />
            设为默认
          </label>
          <div className="model-settings__form-actions">
            <button type="submit" disabled={pending}>
              {pending ? '保存中…' : '保存'}
            </button>
            <button type="button" disabled={pending} onClick={backToList}>取消</button>
          </div>
        </form>
      )}
    </dialog>
  );
}
