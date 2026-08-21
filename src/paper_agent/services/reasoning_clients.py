from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from paper_agent.model_profile_storage import ModelProfileRepository
from paper_agent.model_profiles import ModelProfile, ModelSnapshot
from paper_agent.models.vllm import (
    VllmChatClient,
    VllmModelConfig,
    VllmStructuredClient,
    VllmToolCallingClient,
    _create_openai_client,
)
from paper_agent.services.model_secrets import ModelSecretStore

if TYPE_CHECKING:
    from openai import OpenAI


ENVIRONMENT_FALLBACK_PROFILE_ID = "00000000-0000-0000-0000-000000000000"
_CACHE_LIMIT = 16


class ReasoningClientResolutionError(RuntimeError):
    """Raised when a usable reasoning-model client cannot be resolved."""


@dataclass(frozen=True)
class ResolvedReasoningClients:
    profile: ModelProfile
    snapshot: ModelSnapshot
    chat: VllmChatClient
    structured: VllmStructuredClient
    tools: VllmToolCallingClient


def is_read_only_model_profile(profile_id: str) -> bool:
    """Return whether a reserved synthetic profile must reject mutations."""
    return profile_id == ENVIRONMENT_FALLBACK_PROFILE_ID


class ReasoningClientProvider:
    def __init__(
        self,
        repository: ModelProfileRepository,
        secrets: ModelSecretStore,
        reasoning_model: VllmModelConfig | None = None,
        *,
        client_factory: Callable[[VllmModelConfig], OpenAI] | None = None,
    ) -> None:
        self.repository = repository
        self.secrets = secrets
        self.reasoning_model = reasoning_model
        self.client_factory = client_factory
        self._cache: OrderedDict[tuple[str, int], ResolvedReasoningClients] = OrderedDict()

    def resolve(self, profile_id: str) -> ResolvedReasoningClients:
        profile, config = self._active_profile_and_config(profile_id)
        cache_key = (profile.id, profile.revision)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached

        failed = False
        try:
            transport = self._create_client(config)
            resolved = ResolvedReasoningClients(
                profile=profile,
                snapshot=profile.snapshot(),
                chat=VllmChatClient(config, client=transport),
                structured=VllmStructuredClient(config, client=transport),
                tools=VllmToolCallingClient(config, client=transport),
            )
        except Exception:
            failed = True
        if failed:
            raise ReasoningClientResolutionError("Reasoning model client is unavailable.")
        self._cache[cache_key] = resolved
        self._cache.move_to_end(cache_key)
        if len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return resolved

    def is_read_only_profile(self, profile_id: str) -> bool:
        return is_read_only_model_profile(profile_id)

    def environment_fallback_profile(self) -> ModelProfile | None:
        if self.reasoning_model is None or any(
            profile.enabled for profile in self.repository.list_active()
        ):
            return None
        return self._environment_fallback()[0]

    def _active_profile_and_config(self, profile_id: str) -> tuple[ModelProfile, VllmModelConfig]:
        if profile_id == ENVIRONMENT_FALLBACK_PROFILE_ID:
            return self._environment_fallback()

        profile = self.repository.get(profile_id)
        if profile is None:
            raise ReasoningClientResolutionError("Model profile was not found.")
        if profile.deleted_at is not None:
            raise ReasoningClientResolutionError("Model profile is deleted.")
        if not profile.enabled:
            raise ReasoningClientResolutionError("Model profile is disabled.")
        if profile.secret_ref is None:
            api_key = "EMPTY"
        else:
            credentials_failed = False
            api_key = ""
            try:
                api_key = self.secrets.get(profile.secret_ref)
            except Exception:
                credentials_failed = True
            if credentials_failed or not api_key:
                raise ReasoningClientResolutionError("Model profile credentials are unavailable.")
        return profile, VllmModelConfig(
            base_url=profile.base_url,
            model=profile.model_name,
            api_key=api_key,
        )

    def _environment_fallback(self) -> tuple[ModelProfile, VllmModelConfig]:
        if self.reasoning_model is None or any(
            profile.enabled for profile in self.repository.list_active()
        ):
            raise ReasoningClientResolutionError("Model profile was not found.")
        config = self.reasoning_model
        return (
            ModelProfile(
                id=ENVIRONMENT_FALLBACK_PROFILE_ID,
                display_name="Environment reasoning model",
                base_url=config.base_url,
                model_name=config.model,
                is_default=True,
            ),
            config,
        )

    def _create_client(self, config: VllmModelConfig) -> OpenAI:
        if self.client_factory is None:
            return _create_openai_client(config)
        return self.client_factory(config)
