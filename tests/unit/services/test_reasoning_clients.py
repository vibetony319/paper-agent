from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from paper_agent.model_profile_storage import ModelProfileRepository
from paper_agent.model_profiles import ModelProfile, ModelProfileChanges
from paper_agent.models import VllmModelConfig
from paper_agent.services.model_secrets import ModelSecretStore
from paper_agent.services.reasoning_clients import (
    ENVIRONMENT_FALLBACK_PROFILE_ID,
    ReasoningClientProvider,
    ReasoningClientResolutionError,
)


class FakeOpenAI:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **_kwargs: object) -> object:
        raise AssertionError("this test only resolves clients")


@pytest.fixture
def repository(tmp_path: Path) -> ModelProfileRepository:
    return ModelProfileRepository(f"sqlite:///{tmp_path / 'model-profiles.db'}")


@pytest.fixture
def secrets(tmp_path: Path) -> ModelSecretStore:
    return ModelSecretStore(tmp_path / "secrets.json")


@pytest.fixture
def client_factory():
    created: list[tuple[VllmModelConfig, FakeOpenAI]] = []

    def create(config: VllmModelConfig) -> FakeOpenAI:
        client = FakeOpenAI()
        created.append((config, client))
        return client

    create.created = created
    return create


def _profile(**changes: object) -> ModelProfile:
    values: dict[str, object] = {
        "display_name": "Local Qwen",
        "base_url": "http://127.0.0.1:8000/v1",
        "model_name": "qwen-test",
    }
    values.update(changes)
    return ModelProfile(**values)


def _exception_chain_text(error: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return "\n".join(messages)


def test_provider_caches_by_profile_revision(repository, secrets, client_factory):
    """Breaks if a profile edit can reuse clients built for an older revision."""
    profile = repository.create(_profile())
    provider = ReasoningClientProvider(repository, secrets, client_factory=client_factory)

    first = provider.resolve(profile.id)
    second = provider.resolve(profile.id)
    updated = repository.update(
        profile.id,
        expected_revision=profile.revision,
        changes=ModelProfileChanges(display_name="Renamed Qwen"),
    )
    third = provider.resolve(updated.id)

    assert first is second
    assert third is not first
    assert third.snapshot.revision == 2


def test_provider_serializes_concurrent_cache_miss_construction(
    repository, secrets
):
    """Breaks if concurrent /test requests build and publish duplicate client bundles."""
    profile = repository.create(_profile())
    first_factory_entered = Event()
    second_factory_entered = Event()
    calls_lock = Lock()
    calls: list[VllmModelConfig] = []

    def blocking_factory(config: VllmModelConfig) -> FakeOpenAI:
        with calls_lock:
            calls.append(config)
            call_number = len(calls)
        if call_number == 1:
            first_factory_entered.set()
            second_factory_entered.wait(timeout=1)
        else:
            second_factory_entered.set()
        return FakeOpenAI()

    provider = ReasoningClientProvider(
        repository, secrets, client_factory=blocking_factory
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(provider.resolve, profile.id)
        assert first_factory_entered.wait(timeout=1)
        second_future = executor.submit(provider.resolve, profile.id)
        first = first_future.result(timeout=2)
        second = second_future.result(timeout=2)

    assert first is second
    assert len(calls) == 1


def test_provider_uses_one_transport_for_every_client_in_a_resolved_bundle(
    repository, secrets, client_factory
):
    """Breaks if one resolve creates partial independent transports for its three clients."""
    profile = repository.create(_profile())

    resolved = ReasoningClientProvider(
        repository, secrets, client_factory=client_factory
    ).resolve(profile.id)

    assert len(client_factory.created) == 1
    transport = client_factory.created[0][1]
    assert resolved.chat.client is transport
    assert resolved.structured.client is transport
    assert resolved.tools.client is transport


def test_provider_revalidates_cached_profiles_before_returning_them(
    repository, secrets, client_factory
):
    """Breaks if disabling or deleting a profile can revive an old cached client."""
    profile = repository.create(_profile())
    provider = ReasoningClientProvider(repository, secrets, client_factory=client_factory)
    provider.resolve(profile.id)

    disabled = repository.update(
        profile.id,
        expected_revision=profile.revision,
        changes=ModelProfileChanges(enabled=False),
    )
    with pytest.raises(ReasoningClientResolutionError, match="disabled"):
        provider.resolve(profile.id)

    deleted = repository.soft_delete(profile.id, expected_revision=disabled.revision)
    with pytest.raises(ReasoningClientResolutionError, match="deleted"):
        provider.resolve(deleted.id)


def test_provider_evicts_the_least_recently_used_entry_after_sixteen_profiles(
    repository, secrets, client_factory
):
    """Breaks if resolving more than sixteen profiles leaves the oldest cache entry alive."""
    profiles = [repository.create(_profile(display_name=f"Model {index}")) for index in range(17)]
    provider = ReasoningClientProvider(repository, secrets, client_factory=client_factory)

    first = provider.resolve(profiles[0].id)
    for profile in profiles[1:]:
        provider.resolve(profile.id)
    replacement = provider.resolve(profiles[0].id)

    assert replacement is not first


def test_environment_fallback_is_read_only_and_uses_no_database_secret(
    repository, secrets, client_factory
):
    """Breaks if fallback persists a profile/key or becomes available beside an enabled profile."""
    fallback = VllmModelConfig(
        base_url="http://127.0.0.1:8000/v1", model="env-qwen", api_key="env-key"
    )
    provider = ReasoningClientProvider(
        repository,
        secrets,
        reasoning_model=fallback,
        client_factory=client_factory,
    )

    resolved = provider.resolve(ENVIRONMENT_FALLBACK_PROFILE_ID)

    assert resolved.profile.id == ENVIRONMENT_FALLBACK_PROFILE_ID
    assert resolved.profile.secret_ref is None
    assert resolved.snapshot.model_name == "env-qwen"
    assert provider.is_read_only_profile(ENVIRONMENT_FALLBACK_PROFILE_ID) is True
    assert repository.get(ENVIRONMENT_FALLBACK_PROFILE_ID) is None
    assert client_factory.created[0][0].api_key == "env-key"

    repository.create(_profile())
    with pytest.raises(ReasoningClientResolutionError, match="not found"):
        provider.resolve(ENVIRONMENT_FALLBACK_PROFILE_ID)


def test_provider_wraps_client_factory_errors_without_exposing_configuration_secrets(
    repository, secrets
):
    """Breaks if client construction exposes provider configuration through public errors."""
    profile = repository.create(_profile())

    def fail_factory(_config: VllmModelConfig) -> FakeOpenAI:
        raise RuntimeError("provider-key-secret")

    with pytest.raises(ReasoningClientResolutionError) as caught:
        ReasoningClientProvider(repository, secrets, client_factory=fail_factory).resolve(profile.id)

    assert "provider-key-secret" not in _exception_chain_text(caught.value)


def test_provider_rejects_a_missing_secret_for_a_referenced_profile(repository, secrets, client_factory):
    """Breaks if a configured secret reference silently downgrades to the EMPTY API key."""
    profile = repository.create(
        _profile(secret_ref="model-profile:00000000-0000-4000-8000-000000000001")
    )
    provider = ReasoningClientProvider(repository, secrets, client_factory=client_factory)

    with pytest.raises(ReasoningClientResolutionError, match="credentials are unavailable"):
        provider.resolve(profile.id)

    assert client_factory.created == []
