from dataclasses import asdict

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
