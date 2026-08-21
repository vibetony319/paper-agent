from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from paper_agent.model_profile_storage import (
    ModelProfileRepository,
    ModelProfileRevisionError,
)
from paper_agent.model_profiles import (
    ModelCapabilities,
    ModelProfile,
    ModelProfileChanges,
)


@pytest.fixture
def repository(tmp_path):
    return ModelProfileRepository(f"sqlite:///{tmp_path / 'model-profiles.db'}")


def _profile(**changes) -> ModelProfile:
    values = {
        "display_name": "Local Qwen",
        "base_url": "http://127.0.0.1:8000/v1",
        "model_name": "qwen-test",
    }
    values.update(changes)
    return ModelProfile(**values)


def test_profile_normalizes_names_and_urls_before_persistence(repository):
    """Breaks if callers can persist leading or trailing whitespace in model identity."""
    created = repository.create(
        _profile(
            display_name="  Local Qwen  ",
            base_url="  http://127.0.0.1:8000/v1  ",
            model_name="  qwen-test  ",
        )
    )

    assert created.display_name == "Local Qwen"
    assert created.base_url == "http://127.0.0.1:8000/v1"
    assert created.model_name == "qwen-test"
    assert repository.get(created.id) == created


def test_profile_rejects_an_empty_model_name():
    """Breaks if a profile cannot name a model but is still accepted for later requests."""
    with pytest.raises(ValueError, match="model name"):
        _profile(model_name="   ")


def test_profile_rejects_non_http_base_urls():
    """Breaks if a profile can direct the backend to an unintended URL scheme."""
    with pytest.raises(ValueError, match="base URL"):
        _profile(base_url="file:///tmp/vllm")


@pytest.mark.parametrize(
    "base_url",
    (
        "https://api-key@localhost:8000/v1",
        "https://localhost:8000/v1?api_key=top-secret",
        "https://localhost:8000/v1#top-secret",
    ),
)
def test_profile_rejects_base_urls_that_can_embed_credentials(base_url):
    """Breaks if API-key material can be persisted through a model URL."""
    with pytest.raises(ValueError, match="base URL"):
        _profile(base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    (
        "http://:8000/v1",
        "http://localhost:not-a-port/v1",
        "http://[unclosed-host/v1",
    ),
)
def test_profile_rejects_malformed_base_url_hosts_and_ports(base_url):
    """Breaks if a malformed vLLM endpoint reaches persistence or later client setup."""
    with pytest.raises(ValueError, match="base URL"):
        _profile(base_url=base_url)


def test_profile_and_changes_reject_non_reference_secret_values_without_echoing_them():
    """Breaks if secret material can enter domain reprs or SQLite through secret_ref."""
    hostile_secret = "sk-live-secret"

    with pytest.raises(ValueError) as profile_error:
        _profile(secret_ref=hostile_secret)
    with pytest.raises(ValueError) as changes_error:
        ModelProfileChanges(secret_ref=hostile_secret)

    assert hostile_secret not in str(profile_error.value)
    assert hostile_secret not in str(changes_error.value)


def test_repository_rejects_invalid_secret_refs_on_create_and_update(repository):
    """Breaks if an invalid ref can be written by either repository mutation path."""
    profile = repository.create(_profile())

    with pytest.raises(ValueError):
        repository.create(_profile(secret_ref="sk-live-secret"))
    with pytest.raises(ValueError):
        repository.update(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(secret_ref="sk-live-secret"),
        )

    assert repository.get(profile.id) == profile


def test_profile_rejects_naive_public_datetimes():
    """Breaks if timestamp serialization depends on the host machine timezone."""
    naive = datetime(2026, 8, 22, 9, 30)

    with pytest.raises(ValueError, match="timezone-aware"):
        ModelCapabilities(checked_at=naive)
    for field_name in ("created_at", "updated_at", "deleted_at"):
        with pytest.raises(ValueError, match="timezone-aware"):
            _profile(**{field_name: naive})


def test_profile_normalizes_aware_public_datetimes_to_utc():
    """Breaks if equivalent public timestamps persist with host-dependent offsets."""
    china_time = datetime(2026, 8, 22, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    profile = _profile(
        created_at=china_time,
        updated_at=china_time,
        capabilities=ModelCapabilities(checked_at=china_time),
    )

    assert profile.created_at == datetime(2026, 8, 22, 1, 30, tzinfo=UTC)
    assert profile.updated_at == datetime(2026, 8, 22, 1, 30, tzinfo=UTC)
    assert profile.capabilities.checked_at == datetime(2026, 8, 22, 1, 30, tzinfo=UTC)


def test_repository_enforces_one_active_default_profile(repository):
    """Breaks if two active profiles can simultaneously be selected as default."""
    repository.create(_profile(display_name="First", is_default=True))

    with pytest.raises(IntegrityError):
        repository.create(_profile(display_name="Second", is_default=True))


def test_repository_rejects_stale_profile_updates(repository):
    """Breaks if a stale editor can overwrite a newer profile revision."""
    profile = repository.create(_profile())
    updated = repository.update(
        profile.id,
        expected_revision=profile.revision,
        changes=ModelProfileChanges(display_name="Updated"),
    )

    with pytest.raises(ModelProfileRevisionError):
        repository.update(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(display_name="Stale"),
        )

    assert updated.display_name == "Updated"
    assert updated.revision == 2


def test_soft_delete_removes_profile_from_active_listing(repository):
    """Breaks if logically deleted profiles remain selectable as active defaults."""
    profile = repository.create(_profile(display_name="本地 Qwen", is_default=True))

    deleted = repository.soft_delete(profile.id, expected_revision=profile.revision)

    assert deleted.deleted_at is not None
    assert deleted.is_default is False
    assert deleted.revision == 2
    assert repository.list_active() == ()
    assert repository.get(profile.id) == deleted


def test_profile_snapshot_omits_secret_reference_and_secret_value():
    """Breaks if a request snapshot can serialize a key reference or secret material."""
    profile = _profile(secret_ref="model-profile:00000000-0000-4000-8000-000000000001")

    snapshot = profile.snapshot()

    assert asdict(snapshot) == {
        "profile_id": profile.id,
        "display_name": "Local Qwen",
        "base_url": "http://127.0.0.1:8000/v1",
        "model_name": "qwen-test",
        "revision": 1,
    }


def test_update_capabilities_uses_the_same_revision_guard(repository):
    """Breaks if connection-test results can overwrite a profile after an intervening edit."""
    profile = repository.create(_profile())
    capabilities = ModelCapabilities(basic_chat=True, structured_output=True)

    updated = repository.update_capabilities(
        profile.id,
        expected_revision=profile.revision,
        capabilities=capabilities,
    )

    assert updated.capabilities == capabilities
    assert updated.revision == 2
    with pytest.raises(ModelProfileRevisionError):
        repository.update_capabilities(
            profile.id,
            expected_revision=profile.revision,
            capabilities=ModelCapabilities(tool_calling=True),
        )


def test_set_default_clears_previous_default_and_revises_both_profiles(repository):
    """Breaks if switching defaults leaves the former default selected or unversioned."""
    first = repository.create(_profile(display_name="First", is_default=True))
    second = repository.create(_profile(display_name="Second"))

    selected = repository.set_default(second.id, expected_revision=second.revision)

    assert selected.is_default is True
    assert selected.revision == 2
    previous = repository.get(first.id)
    assert previous is not None
    assert previous.is_default is False
    assert previous.revision == 2
