from datetime import UTC, datetime
from pathlib import Path

import pytest

import paper_agent.services.model_profiles as model_profile_services

from paper_agent.model_profile_storage import ModelProfileRepository, ModelProfileRevisionError
from paper_agent.model_profiles import ModelCapabilities, ModelProfile, ModelProfileChanges
from paper_agent.services.model_profiles import ModelProfileService
from paper_agent.services.model_secrets import ModelSecretStore, ModelSecretStoreError
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID


class ProbeChat:
    def __init__(self, response: str | Exception, on_complete=None) -> None:
        self.response = response
        self.on_complete = on_complete

    def complete(self, _messages: list[dict[str, str]]) -> str:
        if self.on_complete is not None:
            self.on_complete()
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ProbeStructured:
    def __init__(self, response: dict[str, object] | Exception) -> None:
        self.response = response

    def generate_json(self, **_kwargs: object) -> dict[str, object]:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ProbeTools:
    def __init__(self, response: Exception | None = None) -> None:
        self.response = response
        self.calls = 0

    def validate_tool_calling(self) -> None:
        self.calls += 1
        if self.response is not None:
            raise self.response


class StaticProvider:
    def __init__(self, resolved: object, *, read_only: bool = False) -> None:
        self.resolved = resolved
        self.read_only = read_only

    def resolve(self, _profile_id: str) -> object:
        return self.resolved

    def is_read_only_profile(self, _profile_id: str) -> bool:
        return self.read_only


class Resolved:
    def __init__(self, profile: ModelProfile, chat, structured, tools) -> None:
        self.profile = profile
        self.chat = chat
        self.structured = structured
        self.tools = tools


class FailingGetSecretStore:
    def __init__(self, delegate: ModelSecretStore) -> None:
        self.delegate = delegate
        self.get_calls = 0

    def set(self, profile_id: str, secret: str) -> str:
        return self.delegate.set(profile_id, secret)

    def get(self, secret_ref: str | None) -> str:
        self.get_calls += 1
        raise ModelSecretStoreError()

    def delete(self, secret_ref: str | None) -> None:
        self.delegate.delete(secret_ref)


@pytest.fixture
def repository(tmp_path: Path) -> ModelProfileRepository:
    return ModelProfileRepository(f"sqlite:///{tmp_path / 'model-profiles.db'}")


def _profile(**changes: object) -> ModelProfile:
    values: dict[str, object] = {
        "display_name": "Local Qwen",
        "base_url": "http://127.0.0.1:8000/v1",
        "model_name": "qwen-test",
    }
    values.update(changes)
    return ModelProfile(**values)


def test_capability_test_short_circuits_after_basic_chat_failure(repository):
    """Breaks if a failed basic request still triggers structured or tool probes."""
    profile = repository.create(_profile())
    tools = ProbeTools()
    service = ModelProfileService(
        repository,
        StaticProvider(
            Resolved(
                profile,
                ProbeChat(RuntimeError("provider-payload-secret")),
                ProbeStructured(RuntimeError("must not run")),
                tools,
            )
        ),
    )

    capabilities = service.test_capabilities(profile.id)

    assert capabilities.basic_chat is False
    assert capabilities.structured_output is False
    assert capabilities.tool_calling is False
    assert capabilities.checked_at is not None
    assert tools.calls == 0
    persisted = repository.get(profile.id)
    assert persisted is not None
    assert persisted.capabilities == capabilities
    assert persisted.revision == 2


def test_capability_test_runs_tool_probe_after_structured_failure(repository):
    """Breaks if a structured-output failure suppresses the independent tool result."""
    profile = repository.create(_profile())
    tools = ProbeTools()
    service = ModelProfileService(
        repository,
        StaticProvider(
            Resolved(
                profile,
                ProbeChat("OK"),
                ProbeStructured(RuntimeError("structured-provider-secret")),
                tools,
            )
        ),
    )

    capabilities = service.test_capabilities(profile.id)

    assert capabilities.basic_chat is True
    assert capabilities.structured_output is False
    assert capabilities.tool_calling is True
    assert tools.calls == 1
    persisted = repository.get(profile.id)
    assert persisted is not None
    assert persisted.capabilities == capabilities


def test_capability_test_requires_exact_ok_payloads(repository):
    """Breaks if a probe accepts arbitrary chat or structured responses as available."""
    profile = repository.create(_profile())
    tools = ProbeTools()
    service = ModelProfileService(
        repository,
        StaticProvider(
            Resolved(profile, ProbeChat("not OK"), ProbeStructured({"status": "bad"}), tools)
        ),
    )

    capabilities = service.test_capabilities(profile.id)

    assert capabilities == repository.get(profile.id).capabilities
    assert capabilities.basic_chat is False
    assert capabilities.structured_output is False
    assert tools.calls == 0


def test_fallback_capabilities_are_returned_without_sqlite_persistence(repository):
    """Breaks if the read-only environment fallback requires a database profile row."""
    fallback = _profile(id=ENVIRONMENT_FALLBACK_PROFILE_ID, display_name="Environment model")
    service = ModelProfileService(
        repository,
        StaticProvider(
            Resolved(fallback, ProbeChat("OK"), ProbeStructured({"status": "ok"}), ProbeTools()),
            read_only=True,
        ),
    )

    capabilities = service.test_capabilities(ENVIRONMENT_FALLBACK_PROFILE_ID)

    assert capabilities.basic_chat is True
    assert capabilities.structured_output is True
    assert capabilities.tool_calling is True
    assert isinstance(capabilities.checked_at, datetime)
    assert capabilities.checked_at.tzinfo is UTC
    assert repository.get(ENVIRONMENT_FALLBACK_PROFILE_ID) is None


def test_capability_test_rejects_a_revision_changed_during_its_probes(repository):
    """Breaks if stale probe results overwrite a profile edit that occurs mid-test."""
    profile = repository.create(_profile())

    def revise_profile() -> None:
        repository.update(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(display_name="Edited while testing"),
        )

    service = ModelProfileService(
        repository,
        StaticProvider(
            Resolved(
                profile,
                ProbeChat("OK", on_complete=revise_profile),
                ProbeStructured({"status": "ok"}),
                ProbeTools(),
            )
        ),
    )

    with pytest.raises(ModelProfileRevisionError):
        service.test_capabilities(profile.id)

    current = repository.get(profile.id)
    assert current is not None
    assert current.display_name == "Edited while testing"
    assert current.revision == 2
    assert current.capabilities == ModelCapabilities()


def test_failed_creation_removes_the_newly_written_secret(repository, tmp_path, monkeypatch):
    """Breaks if a database create failure leaves an orphaned plaintext key."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    service = ModelProfileService(repository, StaticProvider(None), store)
    captured_profile_id: list[str] = []

    def fail_create(profile):
        captured_profile_id.append(profile.id)
        raise RuntimeError("database-create-secret")

    monkeypatch.setattr(repository, "create", fail_create)

    with pytest.raises(RuntimeError):
        service.create_profile(
            display_name="Local",
            base_url="http://127.0.0.1:8000/v1",
            model_name="model",
            api_key="new-secret",
        )

    assert len(captured_profile_id) == 1
    assert store.get(f"model-profile:{captured_profile_id[0]}") == ""


@pytest.mark.parametrize("replacement", ["new-secret", None])
def test_failed_update_restores_secret_state(
    repository, tmp_path, monkeypatch, replacement
):
    """Breaks if a failed CAS overwrites or clears the current profile secret."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    profile = _profile()
    secret_ref = store.set(profile.id, "current-secret")
    profile = repository.create(_profile(id=profile.id, secret_ref=secret_ref))
    service = ModelProfileService(repository, StaticProvider(None), store)

    def fail_update(*_args, **_kwargs):
        raise ModelProfileRevisionError()

    monkeypatch.setattr(repository, "update", fail_update)

    with pytest.raises(ModelProfileRevisionError):
        service.update_profile(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(display_name="Changed"),
            api_key=replacement,
        )

    assert store.get(secret_ref) == "current-secret"
    assert repository.get(profile.id) == profile


def test_api_key_replacement_resets_capabilities_when_secret_ref_is_unchanged(
    repository, tmp_path
):
    """Breaks if replacing a key retains probes because its durable reference is stable."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    capabilities = ModelCapabilities(
        basic_chat=True,
        structured_output=True,
        tool_calling=True,
        checked_at=datetime(2026, 8, 22, tzinfo=UTC),
    )
    profile = _profile(capabilities=capabilities)
    secret_ref = store.set(profile.id, "old-secret")
    profile = repository.create(
        _profile(id=profile.id, secret_ref=secret_ref, capabilities=capabilities)
    )
    service = ModelProfileService(repository, StaticProvider(None), store)

    updated = service.update_profile(
        profile.id,
        expected_revision=profile.revision,
        changes=ModelProfileChanges(),
        api_key="replacement-secret",
    )

    assert updated.profile.secret_ref == secret_ref
    assert updated.profile.capabilities == ModelCapabilities()
    assert store.get(secret_ref) == "replacement-secret"


def test_usage_lease_blocks_delete_but_allows_edits_and_releases_cleanly(
    repository, tmp_path
):
    """Breaks if request use holds the service mutex or permits profile deletion."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    profile = repository.create(_profile())
    service = ModelProfileService(repository, StaticProvider(None), store)

    with service.usage_lease(profile.id):
        edited = service.update_profile(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(display_name="Edited during request"),
        ).profile
        with pytest.raises(model_profile_services.ModelProfileInUseError):
            service.delete_profile(profile.id, expected_revision=profile.revision)

    service.delete_profile(profile.id, expected_revision=edited.revision)
    assert repository.get(profile.id).deleted_at is not None


def test_failed_delete_restores_secret_and_successful_delete_removes_it(
    repository, tmp_path, monkeypatch
):
    """Breaks if delete compensation loses a live key or successful deletion retains it."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    profile = _profile(is_default=True)
    secret_ref = store.set(profile.id, "delete-secret")
    profile = repository.create(_profile(id=profile.id, secret_ref=secret_ref, is_default=True))
    service = ModelProfileService(repository, StaticProvider(None), store)
    real_soft_delete = repository.soft_delete

    def fail_delete(*_args, **_kwargs):
        raise ModelProfileRevisionError()

    monkeypatch.setattr(repository, "soft_delete", fail_delete)
    with pytest.raises(ModelProfileRevisionError):
        service.delete_profile(profile.id, expected_revision=profile.revision)
    assert store.get(secret_ref) == "delete-secret"
    assert repository.get(profile.id) == profile

    monkeypatch.setattr(repository, "soft_delete", real_soft_delete)
    service.delete_profile(profile.id, expected_revision=profile.revision)
    assert store.get(secret_ref) == ""


def test_update_preflights_secret_state_before_database_commit(repository, tmp_path):
    """Breaks if a failed response-view key read occurs after an update commits."""
    real_store = ModelSecretStore(tmp_path / "secrets.json")
    profile = _profile()
    secret_ref = real_store.set(profile.id, "current-secret")
    profile = repository.create(_profile(id=profile.id, secret_ref=secret_ref))
    store = FailingGetSecretStore(real_store)
    service = ModelProfileService(repository, StaticProvider(None), store)

    with pytest.raises(ModelSecretStoreError):
        service.update_profile(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(display_name="Must not commit"),
        )

    assert store.get_calls == 1
    assert repository.get(profile.id) == profile


def test_set_default_preflights_secret_state_before_database_commit(
    repository, tmp_path
):
    """Breaks if a failed response-view key read occurs after a default switch commits."""
    first = repository.create(_profile(display_name="First", is_default=True))
    real_store = ModelSecretStore(tmp_path / "secrets.json")
    second = _profile(display_name="Second")
    secret_ref = real_store.set(second.id, "second-secret")
    second = repository.create(
        _profile(id=second.id, display_name="Second", secret_ref=secret_ref)
    )
    store = FailingGetSecretStore(real_store)
    service = ModelProfileService(repository, StaticProvider(None), store)

    with pytest.raises(ModelSecretStoreError):
        service.set_default(second.id, expected_revision=second.revision)

    assert store.get_calls == 1
    assert repository.get(first.id) == first
    assert repository.get(second.id) == second


def test_create_builds_response_from_known_key_state_without_read_back(
    repository, tmp_path
):
    """Breaks if successful create reads its just-written key after the DB commit."""
    real_store = ModelSecretStore(tmp_path / "secrets.json")
    store = FailingGetSecretStore(real_store)
    service = ModelProfileService(repository, StaticProvider(None), store)

    created = service.create_profile(
        display_name="Created",
        base_url="http://127.0.0.1:8000/v1",
        model_name="model",
        api_key="known-secret",
    )

    assert created.has_api_key is True
    assert created.api_key_mask == "••••••••"
    assert store.get_calls == 0
    assert repository.get(created.profile.id) == created.profile


def test_connection_test_does_not_read_secret_or_change_profile(
    repository, tmp_path, monkeypatch
):
    """A network-only check works even when the stored key cannot be read."""
    real_store = ModelSecretStore(tmp_path / "secrets.json")
    profile = _profile()
    secret_ref = real_store.set(profile.id, "probe-secret")
    profile = repository.create(_profile(id=profile.id, secret_ref=secret_ref))
    store = FailingGetSecretStore(real_store)
    service = ModelProfileService(
        repository,
        StaticProvider(
            Resolved(
                profile,
                ProbeChat("OK"),
                ProbeStructured({"status": "ok"}),
                ProbeTools(),
            )
        ),
        store,
    )

    monkeypatch.setattr(
        model_profile_services.httpx,
        "head",
        lambda *_args, **_kwargs: type("Response", (), {"status_code": 200})(),
    )
    assert service.test_profile(profile.id, expected_revision=profile.revision) == 200

    assert store.get_calls == 0
    assert repository.get(profile.id) == profile


def test_default_create_set_failure_does_not_change_existing_default(
    repository, tmp_path, monkeypatch
):
    """Breaks if a secret set failure reaches the combined default DB transaction."""
    current = repository.create(_profile(display_name="Current", is_default=True))
    store = ModelSecretStore(tmp_path / "secrets.json")
    service = ModelProfileService(repository, StaticProvider(None), store)

    def fail_set(_profile_id: str, _secret: str) -> str:
        raise ModelSecretStoreError()

    monkeypatch.setattr(store, "set", fail_set)

    with pytest.raises(ModelSecretStoreError):
        service.create_profile(
            display_name="Replacement",
            base_url="http://127.0.0.1:8000/v1",
            model_name="model",
            api_key="new-secret",
            is_default=True,
        )

    assert repository.list_active() == (current,)


def test_failed_combined_default_create_compensates_new_secret(
    repository, tmp_path, monkeypatch
):
    """Breaks if create_replacing_default failure leaves a key or changes the default."""
    current = repository.create(_profile(display_name="Current", is_default=True))
    store = ModelSecretStore(tmp_path / "secrets.json")
    service = ModelProfileService(repository, StaticProvider(None), store)
    attempted_ids: list[str] = []

    def fail_create(profile: ModelProfile) -> ModelProfile:
        attempted_ids.append(profile.id)
        raise RuntimeError("combined-create-failure-secret")

    monkeypatch.setattr(repository, "create_replacing_default", fail_create)

    with pytest.raises(RuntimeError):
        service.create_profile(
            display_name="Replacement",
            base_url="http://127.0.0.1:8000/v1",
            model_name="model",
            api_key="new-secret",
            is_default=True,
        )

    assert len(attempted_ids) == 1
    assert store.get(f"model-profile:{attempted_ids[0]}") == ""
    assert repository.list_active() == (current,)


def test_default_delete_secret_failure_prevents_delete_and_promotion(
    repository, tmp_path, monkeypatch
):
    """Breaks if a failed key delete still commits the combined default transition."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    current = _profile(display_name="Current", is_default=True)
    secret_ref = store.set(current.id, "current-secret")
    current = repository.create(
        _profile(
            id=current.id,
            display_name="Current",
            is_default=True,
            secret_ref=secret_ref,
        )
    )
    replacement = repository.create(_profile(display_name="Replacement"))
    service = ModelProfileService(repository, StaticProvider(None), store)

    def fail_delete(_secret_ref: str | None) -> None:
        raise ModelSecretStoreError()

    monkeypatch.setattr(store, "delete", fail_delete)

    with pytest.raises(ModelSecretStoreError):
        service.delete_profile(current.id, expected_revision=current.revision)

    assert store.get(secret_ref) == "current-secret"
    assert repository.get(current.id) == current
    assert repository.get(replacement.id) == replacement


def test_failed_combined_default_delete_restores_secret_and_database_state(
    repository, tmp_path, monkeypatch
):
    """Breaks if promotion failure loses the deleted key or partially deletes default."""
    store = ModelSecretStore(tmp_path / "secrets.json")
    current = _profile(display_name="Current", is_default=True)
    secret_ref = store.set(current.id, "current-secret")
    current = repository.create(
        _profile(
            id=current.id,
            display_name="Current",
            is_default=True,
            secret_ref=secret_ref,
        )
    )
    replacement = repository.create(_profile(display_name="Replacement"))
    service = ModelProfileService(repository, StaticProvider(None), store)

    def fail_combined_delete(*_args, **_kwargs) -> ModelProfile:
        raise ModelProfileRevisionError()

    monkeypatch.setattr(
        repository, "soft_delete_and_set_default", fail_combined_delete
    )

    with pytest.raises(ModelProfileRevisionError):
        service.delete_profile(current.id, expected_revision=current.revision)

    assert store.get(secret_ref) == "current-secret"
    assert repository.get(current.id) == current
    assert repository.get(replacement.id) == replacement
