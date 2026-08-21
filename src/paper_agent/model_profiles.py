from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4


MODEL_SECRET_REFERENCE_PREFIX = "model-profile:"


def validate_model_profile_id(profile_id: str) -> str:
    if not isinstance(profile_id, str):
        raise ValueError("model profile ID must be a canonical UUID")
    try:
        parsed = UUID(profile_id)
    except (ValueError, AttributeError):
        raise ValueError("model profile ID must be a canonical UUID") from None
    if str(parsed) != profile_id:
        raise ValueError("model profile ID must be a canonical UUID")
    return profile_id


def parse_model_secret_reference(secret_ref: str) -> str:
    if not isinstance(secret_ref, str) or not secret_ref.startswith(
        MODEL_SECRET_REFERENCE_PREFIX
    ):
        raise ValueError("model secret reference is invalid")
    return validate_model_profile_id(
        secret_ref.removeprefix(MODEL_SECRET_REFERENCE_PREFIX)
    )


def _normalized_required(value: str, label: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"{label} must be nonempty")
    return normalized


def _normalized_base_url(value: str) -> str:
    normalized = _normalized_required(value, "base URL")
    try:
        parsed = urlparse(normalized)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError("base URL must be an HTTP or HTTPS URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must be an HTTP or HTTPS URL")
    return normalized


def _normalized_public_datetime(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ModelCapabilities:
    basic_chat: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    checked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.checked_at is not None:
            object.__setattr__(
                self,
                "checked_at",
                _normalized_public_datetime(self.checked_at, "capabilities checked_at"),
            )


@dataclass(frozen=True)
class ModelSnapshot:
    profile_id: str
    display_name: str
    base_url: str
    model_name: str
    revision: int


@dataclass(frozen=True)
class ModelProfile:
    display_name: str
    base_url: str
    model_name: str
    enabled: bool = True
    is_default: bool = False
    revision: int = 1
    secret_ref: str | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "display_name", _normalized_required(self.display_name, "display name")
        )
        object.__setattr__(self, "base_url", _normalized_base_url(self.base_url))
        object.__setattr__(
            self, "model_name", _normalized_required(self.model_name, "model name")
        )
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.secret_ref is not None:
            parse_model_secret_reference(self.secret_ref)
        if not isinstance(self.capabilities, ModelCapabilities):
            raise ValueError("capabilities must be a ModelCapabilities")
        object.__setattr__(
            self,
            "created_at",
            _normalized_public_datetime(self.created_at, "created_at"),
        )
        object.__setattr__(
            self,
            "updated_at",
            _normalized_public_datetime(self.updated_at, "updated_at"),
        )
        if self.deleted_at is not None:
            object.__setattr__(
                self,
                "deleted_at",
                _normalized_public_datetime(self.deleted_at, "deleted_at"),
            )

    def snapshot(self) -> ModelSnapshot:
        return ModelSnapshot(
            profile_id=self.id,
            display_name=self.display_name,
            base_url=self.base_url,
            model_name=self.model_name,
            revision=self.revision,
        )


@dataclass(frozen=True)
class Unchanged:
    pass


UNCHANGED = Unchanged()


@dataclass(frozen=True)
class ModelProfileChanges:
    display_name: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    secret_ref: str | None | Unchanged = UNCHANGED

    def __post_init__(self) -> None:
        if self.secret_ref is not UNCHANGED and self.secret_ref is not None:
            parse_model_secret_reference(self.secret_ref)
