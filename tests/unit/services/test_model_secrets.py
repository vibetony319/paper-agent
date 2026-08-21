import json
import threading
from pathlib import Path

import pytest

from paper_agent.config import get_settings
from paper_agent.services.model_secrets import ModelSecretStore, ModelSecretStoreError


PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def test_secret_store_round_trips_without_exposing_secret_in_reference(tmp_path):
    """Breaks if secret storage leaks a secret through a reusable profile reference."""
    store = ModelSecretStore(tmp_path / "secrets" / "model-profiles.json")

    reference = store.set(PROFILE_ID, "top-secret")

    assert reference == f"model-profile:{PROFILE_ID}"
    assert store.get(reference) == "top-secret"
    assert "top-secret" not in reference


@pytest.mark.parametrize(
    ("operation", "value"),
    [
        ("set", "profile-a"),
        ("get", "model-profile:profile-a"),
        ("get", "profile-a"),
        ("delete", "model-profile:profile-a"),
    ],
)
def test_secret_store_rejects_invalid_identifiers_without_echoing_them(
    tmp_path, operation, value
):
    """Breaks if malformed references are accepted or user input leaks from an error."""
    store = ModelSecretStore(tmp_path / "secrets" / "model-profiles.json")

    with pytest.raises(ModelSecretStoreError) as caught:
        if operation == "set":
            store.set(value, "top-secret")
        elif operation == "get":
            store.get(value)
        else:
            store.delete(value)

    assert value not in str(caught.value)
    assert "top-secret" not in str(caught.value)


def test_secret_store_raises_safe_error_for_corrupted_file(tmp_path):
    """Breaks if corrupt secret-file contents are exposed while reporting a read failure."""
    path = tmp_path / "secrets" / "model-profiles.json"
    path.parent.mkdir()
    path.write_text('{"broken": "top-secret"', encoding="utf-8")
    store = ModelSecretStore(path)

    with pytest.raises(ModelSecretStoreError) as caught:
        store.get(f"model-profile:{PROFILE_ID}")

    assert "top-secret" not in str(caught.value)
    assert "broken" not in str(caught.value)


def test_secret_store_delete_rewrites_the_file_without_a_temporary_left_behind(tmp_path):
    """Breaks if deleting one profile removes others or leaves a partial replacement file."""
    path = tmp_path / "secrets" / "model-profiles.json"
    store = ModelSecretStore(path)
    second_id = "00000000-0000-4000-8000-000000000002"
    first_reference = store.set(PROFILE_ID, "first-secret")
    second_reference = store.set(second_id, "second-secret")

    store.delete(first_reference)

    assert store.get(first_reference) == ""
    assert store.get(second_reference) == "second-secret"
    assert json.loads(path.read_text(encoding="utf-8")) == {second_id: "second-secret"}
    assert not path.with_suffix(".tmp").exists()


def test_secret_store_keeps_an_existing_legacy_temporary_file_out_of_replacements(tmp_path):
    """Breaks if a fixed temporary path can overwrite unrelated plaintext or collide."""
    path = tmp_path / "secrets" / "model-profiles.json"
    path.parent.mkdir()
    legacy_temporary = path.with_suffix(".tmp")
    legacy_temporary.write_text("unrelated", encoding="utf-8")
    store = ModelSecretStore(path)

    store.set(PROFILE_ID, "top-secret")

    assert store.get(f"model-profile:{PROFILE_ID}") == "top-secret"
    assert legacy_temporary.read_text(encoding="utf-8") == "unrelated"


@pytest.mark.parametrize("failing_method", ("write_text", "chmod", "replace"))
def test_secret_store_cleans_failed_temporary_writes_and_preserves_destination(
    tmp_path, monkeypatch, failing_method
):
    """Breaks if an atomic-write failure leaks a temp key file or damages prior secrets."""
    path = tmp_path / "secrets" / "model-profiles.json"
    store = ModelSecretStore(path)
    reference = store.set(PROFILE_ID, "preserved-secret")

    def fail(*_args, **_kwargs):
        raise OSError("injected failure")

    if failing_method == "write_text":
        def fail_write(path, *_args, **_kwargs):
            path.write_bytes(b"partial-secret")
            raise OSError("injected failure")

        monkeypatch.setattr(Path, failing_method, fail_write)
    else:
        monkeypatch.setattr(Path, failing_method, fail)

    with pytest.raises(ModelSecretStoreError) as caught:
        store.set("00000000-0000-4000-8000-000000000002", "new-secret")

    assert "new-secret" not in str(caught.value)
    assert store.get(reference) == "preserved-secret"
    assert list(path.parent.glob(f"{path.stem}*.tmp")) == []


def test_secret_store_serializes_process_local_set_operations(tmp_path):
    """Breaks if a second in-process set can finish while the shared write lock is held."""
    store = ModelSecretStore(tmp_path / "secrets" / "model-profiles.json")
    started = threading.Event()
    finished = threading.Event()

    def write_secret() -> None:
        started.set()
        store.set(PROFILE_ID, "top-secret")
        finished.set()

    with ModelSecretStore._lock:
        writer = threading.Thread(target=write_secret)
        writer.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.2)

    writer.join(timeout=1)
    assert not writer.is_alive()
    assert finished.is_set()
    assert store.get(f"model-profile:{PROFILE_ID}") == "top-secret"


def test_secret_store_handles_missing_references_without_touching_a_file(tmp_path):
    """Breaks if profiles without keys create a secret file or fail to resolve safely."""
    path = tmp_path / "secrets" / "model-profiles.json"
    store = ModelSecretStore(path)

    assert store.get(None) == ""
    store.delete(None)

    assert not path.exists()


def test_settings_places_model_secrets_below_the_application_data_directory(tmp_path):
    """Breaks if model keys are written outside the application-owned data directory."""
    settings = get_settings(data_dir=tmp_path / "data")

    assert settings.model_secrets_path == tmp_path / "data" / "secrets" / "model-profiles.json"
