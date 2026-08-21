import json
from pathlib import Path
from uuid import UUID


class ModelSecretStoreError(RuntimeError):
    """Raised for malformed model-secret data without exposing sensitive contents."""

    def __init__(self) -> None:
        super().__init__("Model secret storage is unavailable.")


class ModelSecretStore:
    _reference_prefix = "model-profile:"

    def __init__(self, path: Path) -> None:
        self.path = path

    def set(self, profile_id: str, secret: str) -> str:
        profile_id = self._validate_profile_id(profile_id)
        if not isinstance(secret, str):
            raise ModelSecretStoreError()
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
            secrets[self._validate_profile_id(profile_id)] = secret
        return secrets

    def _write_all(self, secrets: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(secrets), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.path)
        except OSError:
            raise ModelSecretStoreError() from None

    @classmethod
    def _profile_id_from_reference(cls, secret_ref: str) -> str:
        if not isinstance(secret_ref, str) or not secret_ref.startswith(cls._reference_prefix):
            raise ModelSecretStoreError()
        return cls._validate_profile_id(secret_ref.removeprefix(cls._reference_prefix))

    @staticmethod
    def _validate_profile_id(profile_id: str) -> str:
        if not isinstance(profile_id, str):
            raise ModelSecretStoreError()
        try:
            parsed = UUID(profile_id)
        except (ValueError, AttributeError):
            raise ModelSecretStoreError() from None
        if str(parsed) != profile_id:
            raise ModelSecretStoreError()
        return profile_id
