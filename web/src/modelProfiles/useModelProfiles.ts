import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, paperApi } from '../api/client';
import type {
  ModelProfile,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
} from '../api/types';

const STORAGE_KEY_PREFIX = 'paper-agent:selected-model:';

const LIST_LOAD_FAILED_MESSAGE = '模型列表加载失败。';

export function selectedModelStorageKey(paperId: string): string {
  return `${STORAGE_KEY_PREFIX}${paperId}`;
}

export type ModelProfilesState = {
  profiles: ModelProfile[];
  selectedProfileId: string | null;
  selectedProfile: ModelProfile | null;
  loading: boolean;
  error: string | null;
  selectProfile(id: string): void;
  refresh(): Promise<void>;
  createProfile(input: ModelProfileCreateInput): Promise<ModelProfile>;
  updateProfile(
    id: string,
    revision: number,
    input: ModelProfileUpdateInput,
  ): Promise<ModelProfile>;
  deleteProfile(id: string, revision: number): Promise<void>;
  testProfile(id: string): Promise<ModelProfile>;
};

function isUsable(profile: ModelProfile): boolean {
  return profile.enabled;
}

function upsertProfile(list: ModelProfile[], updated: ModelProfile): ModelProfile[] {
  const exists = list.some((profile) => profile.id === updated.id);
  const merged = exists
    ? list.map((profile) => (profile.id === updated.id ? updated : profile))
    : [...list, updated];
  if (!updated.is_default) {
    return merged;
  }
  return merged.map((profile) => (
    profile.id !== updated.id && profile.is_default
      ? { ...profile, is_default: false }
      : profile
  ));
}

export function useModelProfiles(paperId: string | null): ModelProfilesState {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const profilesRef = useRef(profiles);
  const loadSequence = useRef(0);

  profilesRef.current = profiles;

  const readStoredPreference = useCallback((): string | null => {
    if (paperId === null) {
      return null;
    }
    try {
      return window.localStorage.getItem(selectedModelStorageKey(paperId));
    } catch {
      return null;
    }
  }, [paperId]);

  const resolveSelection = useCallback((list: ModelProfile[]): string | null => {
    const stored = readStoredPreference();
    if (stored !== null && list.some((profile) => profile.id === stored && isUsable(profile))) {
      return stored;
    }
    const fallback = list.find((profile) => profile.is_default && isUsable(profile))
      ?? list.find(isUsable);
    return fallback?.id ?? null;
  }, [readStoredPreference]);

  const refresh = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError(null);
    try {
      const list = await paperApi.listModelProfiles();
      if (sequence !== loadSequence.current) {
        return;
      }
      setProfiles(list);
    } catch {
      if (sequence !== loadSequence.current) {
        return;
      }
      setProfiles([]);
      setError(LIST_LOAD_FAILED_MESSAGE);
    } finally {
      if (sequence === loadSequence.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, paperId]);

  // Re-validate the selection whenever the profile list or the active paper changes.
  // A paper switch always re-reads that paper's stored preference; a list-only
  // change keeps the current selection while it is still usable.
  const previousPaperIdRef = useRef(paperId);
  useEffect(() => {
    const paperChanged = previousPaperIdRef.current !== paperId;
    previousPaperIdRef.current = paperId;
    setSelectedProfileId((current) => {
      if (
        !paperChanged
        && current !== null
        && profiles.some((profile) => profile.id === current && isUsable(profile))
      ) {
        return current;
      }
      return resolveSelection(profiles);
    });
  }, [profiles, paperId, resolveSelection]);

  const selectProfile = useCallback((id: string) => {
    setSelectedProfileId(id);
    if (paperId === null) {
      return;
    }
    try {
      window.localStorage.setItem(selectedModelStorageKey(paperId), id);
    } catch {
      // The preference is a convenience; ignore storage failures.
    }
  }, [paperId]);

  const createProfile = useCallback(async (input: ModelProfileCreateInput) => {
    const created = await paperApi.createModelProfile(input);
    setProfiles((current) => upsertProfile(current, created));
    return created;
  }, []);

  const updateProfile = useCallback(async (
    id: string,
    revision: number,
    input: ModelProfileUpdateInput,
  ) => {
    const updated = await paperApi.updateModelProfile(id, revision, input);
    setProfiles((current) => upsertProfile(current, updated));
    return updated;
  }, []);

  const deleteProfile = useCallback(async (id: string, revision: number) => {
    await paperApi.deleteModelProfile(id, revision);
    setProfiles((current) => current.filter((profile) => profile.id !== id));
  }, []);

  const testProfile = useCallback(async (id: string) => {
    const profile = profilesRef.current.find((candidate) => candidate.id === id);
    if (profile === undefined) {
      throw new ApiError(404, '模型档案不存在。');
    }
    const tested = await paperApi.testModelProfile(id, profile.revision);
    setProfiles((current) => upsertProfile(current, tested));
    return tested;
  }, []);

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedProfileId) ?? null,
    [profiles, selectedProfileId],
  );

  return {
    profiles,
    selectedProfileId,
    selectedProfile,
    loading,
    error,
    selectProfile,
    refresh,
    createProfile,
    updateProfile,
    deleteProfile,
    testProfile,
  };
}
