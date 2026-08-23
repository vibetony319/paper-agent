import { useId } from 'react';

import type { ModelProfile } from '../api/types';

export interface ModelSelectorProps {
  profiles: ModelProfile[];
  value: string | null;
  onChange: (id: string) => void;
}

export function ModelSelector({ profiles, value, onChange }: ModelSelectorProps) {
  const selectId = useId();
  const enabledProfiles = profiles.filter((profile) => profile.enabled);

  return (
    <div className="model-selector">
      <label className="model-selector__label" htmlFor={selectId}>当前模型</label>
      <select
        id={selectId}
        aria-label="当前模型"
        value={value ?? ''}
        disabled={enabledProfiles.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        {enabledProfiles.length === 0 ? (
          <option value="">无可用模型</option>
        ) : (
          <>
            {value === null && <option value="" disabled>请选择模型</option>}
            {enabledProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.display_name}
              </option>
            ))}
          </>
        )}
      </select>
    </div>
  );
}
