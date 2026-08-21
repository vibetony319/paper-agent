import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from paper_agent.model_profiles import (
    MODEL_SECRET_REFERENCE_PREFIX,
    parse_model_secret_reference,
    validate_model_profile_id,
)


class ModelSecretStoreError(RuntimeError):
    """Raised for malformed model-secret data without exposing sensitive contents."""

    def __init__(self) -> None:
        super().__init__("Model secret storage is unavailable.")


class ModelSecretStore:
    _reference_prefix = MODEL_SECRET_REFERENCE_PREFIX
    _lock = RLock()

    def __init__(self, path: Path) -> None:
        self.path = path

    def set(self, profile_id: str, secret: str) -> str:
        profile_id = self._validated_profile_id(profile_id)
        if not isinstance(secret, str):
            raise ModelSecretStoreError()
        with self._lock:
            secrets = self._read_all()
            secrets[profile_id] = secret
            self._write_all(secrets)
        return f"{self._reference_prefix}{profile_id}"

    def get(self, secret_ref: str | None) -> str:
        if secret_ref is None:
            return ""
        profile_id = self._profile_id_from_reference(secret_ref)
        return self._read_all().get(profile_id, "")

    def delete(self, secret_ref: str | None) -> None:
        if secret_ref is None:
            return
        profile_id = self._profile_id_from_reference(secret_ref)
        with self._lock:
            secrets = self._read_all()
            if profile_id not in secrets:
                return
            del secrets[profile_id]
            self._write_all(secrets)

    def _read_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ModelSecretStoreError() from None
        if not isinstance(loaded, dict):
            raise ModelSecretStoreError()
        secrets: dict[str, str] = {}
        for profile_id, secret in loaded.items():
            if not isinstance(profile_id, str) or not isinstance(secret, str):
                raise ModelSecretStoreError()
            secrets[self._validated_profile_id(profile_id)] = secret
        return secrets

    def _write_all(self, secrets: dict[str, str]) -> None:
        temporary: Path | None = None
        descriptor: int | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(temporary_name)
            os.close(descriptor)
            descriptor = None
            temporary.chmod(0o600)
            temporary.write_text(json.dumps(secrets), encoding="utf-8")
            temporary.replace(self.path)
        except (OSError, TypeError, ValueError):
            raise ModelSecretStoreError() from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def _profile_id_from_reference(cls, secret_ref: str) -> str:
        try:
            return parse_model_secret_reference(secret_ref)
        except ValueError:
            raise ModelSecretStoreError()

    @staticmethod
    def _validated_profile_id(profile_id: str) -> str:
        try:
            return validate_model_profile_id(profile_id)
        except ValueError:
            raise ModelSecretStoreError()
