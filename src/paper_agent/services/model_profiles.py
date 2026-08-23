from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import RLock

from paper_agent.model_profile_storage import (
    ModelProfileRepository,
    ModelProfileRevisionError,
)
from paper_agent.model_profiles import (
    UNCHANGED,
    ModelCapabilities,
    ModelProfile,
    ModelProfileChanges,
    Unchanged,
)
from paper_agent.services.model_secrets import ModelSecretStore
from paper_agent.services.reasoning_clients import (
    ENVIRONMENT_FALLBACK_PROFILE_ID,
    ReasoningClientProvider,
    ResolvedReasoningClients,
)


_BASIC_MESSAGES = [{"role": "user", "content": "Return exactly OK."}]
_STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "ok"}},
    "required": ["status"],
    "additionalProperties": False,
}
_API_KEY_MASK = "••••••••"


class ModelProfileNotFoundError(RuntimeError):
    pass


class ModelProfileReadOnlyError(RuntimeError):
    pass


class ModelProfileInputError(ValueError):
    pass


class ModelProfileInUseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProfileView:
    profile: ModelProfile
    has_api_key: bool
    api_key_mask: str | None
    read_only: bool


class ModelProfileService:
    def __init__(
        self,
        repository: ModelProfileRepository,
        provider: ReasoningClientProvider,
        secrets: ModelSecretStore | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.secrets = secrets
        self._mutation_lock = RLock()
        self._in_use: dict[str, int] = {}

    @contextmanager
    def usage_lease(self, profile_id: str) -> Iterator[None]:
        if self.provider.is_read_only_profile(profile_id):
            yield
            return
        with self._mutation_lock:
            profile = self.repository.get(profile_id)
            if profile is None or profile.deleted_at is not None:
                raise ModelProfileNotFoundError()
            self._in_use[profile_id] = self._in_use.get(profile_id, 0) + 1
        try:
            yield
        finally:
            with self._mutation_lock:
                remaining = self._in_use[profile_id] - 1
                if remaining:
                    self._in_use[profile_id] = remaining
                else:
                    del self._in_use[profile_id]

    def list_profiles(self) -> tuple[ModelProfileView, ...]:
        profiles = self.repository.list_active()
        views = [self._view(profile) for profile in profiles]
        if not any(profile.enabled for profile in profiles):
            fallback = self.provider.environment_fallback_profile()
            if fallback is not None:
                views.append(self._view(fallback))
        return tuple(views)

    def create_profile(
        self,
        *,
        display_name: str,
        base_url: str,
        model_name: str,
        api_key: str | None = None,
        enabled: bool = True,
        is_default: bool = False,
    ) -> ModelProfileView:
        if is_default and not enabled:
            raise ModelProfileInputError("disabled profile cannot be default")
        with self._mutation_lock:
            profile = ModelProfile(
                display_name=display_name,
                base_url=base_url,
                model_name=model_name,
                enabled=enabled,
                is_default=is_default,
            )
            secret_written = False
            normalized_key = self._normalized_api_key(api_key)
            try:
                if normalized_key is not None:
                    secret_ref = self._secret_store().set(profile.id, normalized_key)
                    profile = replace(profile, secret_ref=secret_ref)
                    secret_written = True
                created = (
                    self.repository.create_replacing_default(profile)
                    if profile.is_default
                    else self.repository.create(profile)
                )
            except Exception:
                if secret_written:
                    self._secret_store().delete(profile.secret_ref)
                raise
            return self._view_with_key_state(
                created, has_api_key=self._is_real_api_key(normalized_key or "")
            )

    def update_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        changes: ModelProfileChanges,
        api_key: str | None | Unchanged = UNCHANGED,
    ) -> ModelProfileView:
        self._require_mutable(profile_id)
        with self._mutation_lock:
            current = self._require_current(profile_id, expected_revision)
            if changes.secret_ref is not UNCHANGED:
                raise ModelProfileInputError("secret references are internal")
            resulting_enabled = (
                current.enabled if changes.enabled is None else changes.enabled
            )
            resulting_default = (
                current.is_default if changes.is_default is None else changes.is_default
            )
            if resulting_default and not resulting_enabled:
                if changes.is_default is True:
                    raise ModelProfileInputError("disabled profile cannot be default")
                resulting_default = False
            effective_changes = replace(changes, is_default=resulting_default)

            secret_changed = api_key is not UNCHANGED
            old_secret = ""
            normalized_key: str | None = None
            if secret_changed and current.secret_ref is not None:
                old_secret = self._secret_store().get(current.secret_ref)
            if secret_changed:
                normalized_key = self._normalized_api_key(api_key)
                has_api_key = self._is_real_api_key(normalized_key or "")
            else:
                has_api_key = self._profile_has_api_key(current)
            try:
                if secret_changed:
                    if normalized_key is None:
                        self._secret_store().delete(current.secret_ref)
                        effective_changes = replace(effective_changes, secret_ref=None)
                    else:
                        secret_ref = self._secret_store().set(
                            current.id, normalized_key
                        )
                        effective_changes = replace(
                            effective_changes, secret_ref=secret_ref
                        )
                updated = self.repository.update(
                    profile_id,
                    expected_revision=expected_revision,
                    changes=effective_changes,
                    secret_state_changed=secret_changed,
                )
            except Exception:
                if secret_changed:
                    self._restore_secret(current, old_secret)
                raise
            return self._view_with_key_state(updated, has_api_key=has_api_key)

    def delete_profile(self, profile_id: str, *, expected_revision: int) -> None:
        self._require_mutable(profile_id)
        with self._mutation_lock:
            if self._in_use.get(profile_id, 0):
                raise ModelProfileInUseError()
            current = self._require_current(profile_id, expected_revision)
            replacement_profile_id = None
            if current.is_default:
                replacement_profile_id = next(
                    (
                        profile.id
                        for profile in self.repository.list_active()
                        if profile.id != current.id and profile.enabled
                    ),
                    None,
                )
            old_secret = (
                ""
                if current.secret_ref is None
                else self._secret_store().get(current.secret_ref)
            )
            try:
                self._secret_store().delete(current.secret_ref)
                if replacement_profile_id is None:
                    self.repository.soft_delete(
                        profile_id, expected_revision=expected_revision
                    )
                else:
                    self.repository.soft_delete_and_set_default(
                        profile_id,
                        expected_revision=expected_revision,
                        replacement_profile_id=replacement_profile_id,
                    )
            except Exception:
                self._restore_secret(current, old_secret)
                raise

    def set_default(
        self, profile_id: str, *, expected_revision: int
    ) -> ModelProfileView:
        self._require_mutable(profile_id)
        with self._mutation_lock:
            current = self._require_current(profile_id, expected_revision)
            if not current.enabled:
                raise ModelProfileInputError("disabled profile cannot be default")
            has_api_key = self._profile_has_api_key(current)
            selected = self.repository.set_default(
                profile_id, expected_revision=expected_revision
            )
            return self._view_with_key_state(selected, has_api_key=has_api_key)

    def test_profile(
        self, profile_id: str, *, expected_revision: int
    ) -> ModelProfileView:
        if profile_id != ENVIRONMENT_FALLBACK_PROFILE_ID:
            self._require_current(profile_id, expected_revision)
        resolved = self.provider.resolve(profile_id)
        if resolved.profile.revision != expected_revision:
            raise ModelProfileRevisionError()
        has_api_key = self._profile_has_api_key(resolved.profile)
        capabilities = self._probe_capabilities(resolved)
        if self.provider.is_read_only_profile(resolved.profile.id):
            return self._view_with_key_state(
                replace(resolved.profile, capabilities=capabilities),
                has_api_key=has_api_key,
            )
        updated = self.repository.update_capabilities(
            resolved.profile.id,
            expected_revision=resolved.profile.revision,
            capabilities=capabilities,
        )
        return self._view_with_key_state(updated, has_api_key=has_api_key)

    def resolve_default_clients(self) -> ResolvedReasoningClients | None:
        profiles = self.repository.list_active()
        default = next(
            (profile for profile in profiles if profile.enabled and profile.is_default),
            None,
        )
        if default is not None:
            return self.provider.resolve(default.id)
        if not any(profile.enabled for profile in profiles):
            fallback = self.provider.environment_fallback_profile()
            if fallback is not None:
                return self.provider.resolve(ENVIRONMENT_FALLBACK_PROFILE_ID)
        return None

    def test_capabilities(self, profile_id: str) -> ModelCapabilities:
        resolved = self.provider.resolve(profile_id)
        capabilities = self._probe_capabilities(resolved)
        if self.provider.is_read_only_profile(resolved.profile.id):
            return capabilities
        return self.repository.update_capabilities(
            resolved.profile.id,
            expected_revision=resolved.profile.revision,
            capabilities=capabilities,
        ).capabilities

    def _probe_capabilities(self, resolved: object) -> ModelCapabilities:
        basic_chat = self._test_basic_chat(resolved.chat)
        structured_output = False
        tool_calling = False
        if basic_chat:
            structured_output = self._test_structured_output(resolved.structured)
            tool_calling = self._test_tool_calling(resolved.tools)
        capabilities = ModelCapabilities(
            basic_chat=basic_chat,
            structured_output=structured_output,
            tool_calling=tool_calling,
            checked_at=datetime.now(UTC),
        )
        return capabilities

    def _view(self, profile: ModelProfile) -> ModelProfileView:
        return self._view_with_key_state(
            profile, has_api_key=self._profile_has_api_key(profile)
        )

    def _profile_has_api_key(self, profile: ModelProfile) -> bool:
        read_only = self.provider.is_read_only_profile(profile.id)
        if read_only:
            config = self.provider.reasoning_model
            api_key = "" if config is None else config.api_key
        elif profile.secret_ref is None:
            api_key = ""
        else:
            api_key = self._secret_store().get(profile.secret_ref)
        return self._is_real_api_key(api_key)

    def _view_with_key_state(
        self, profile: ModelProfile, *, has_api_key: bool
    ) -> ModelProfileView:
        return ModelProfileView(
            profile=profile,
            has_api_key=has_api_key,
            api_key_mask=_API_KEY_MASK if has_api_key else None,
            read_only=self.provider.is_read_only_profile(profile.id),
        )

    def _require_mutable(self, profile_id: str) -> None:
        if self.provider.is_read_only_profile(profile_id):
            raise ModelProfileReadOnlyError()

    def _require_current(
        self, profile_id: str, expected_revision: int
    ) -> ModelProfile:
        profile = self.repository.get(profile_id)
        if profile is None or profile.deleted_at is not None:
            raise ModelProfileNotFoundError()
        if profile.revision != expected_revision:
            raise ModelProfileRevisionError()
        return profile

    def _restore_secret(self, profile: ModelProfile, old_secret: str) -> None:
        if profile.secret_ref is None:
            self._secret_store().delete(f"model-profile:{profile.id}")
        else:
            self._secret_store().set(profile.id, old_secret)

    def _secret_store(self) -> ModelSecretStore:
        if self.secrets is None:
            candidate = getattr(self.provider, "secrets", None)
            if candidate is None:
                raise RuntimeError("model secret store is unavailable")
            return candidate
        return self.secrets

    @staticmethod
    def _normalized_api_key(api_key: str | None | Unchanged) -> str | None:
        if api_key is UNCHANGED:
            raise ModelProfileInputError("api key state is unchanged")
        if api_key is None or not api_key.strip() or api_key == "EMPTY":
            return None
        return api_key

    @staticmethod
    def _is_real_api_key(api_key: str) -> bool:
        return bool(api_key) and api_key != "EMPTY"

    @staticmethod
    def _test_basic_chat(chat: object) -> bool:
        try:
            return chat.complete(_BASIC_MESSAGES) == "OK"
        except Exception:
            return False

    @staticmethod
    def _test_structured_output(structured: object) -> bool:
        try:
            return structured.generate_json(
                system_prompt="Return the requested JSON object.",
                user_prompt="Return the status object.",
                schema_name="capability_status",
                schema=_STRUCTURED_SCHEMA,
            ) == {"status": "ok"}
        except Exception:
            return False

    @staticmethod
    def _test_tool_calling(tools: object) -> bool:
        try:
            tools.validate_tool_calling()
            return True
        except Exception:
            return False
