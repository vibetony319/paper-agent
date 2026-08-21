from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4


def _normalized_required(value: str, label: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValueError(f"{label} must be nonempty")
    return normalized


def _normalized_base_url(value: str) -> str:
    normalized = _normalized_required(value, "base URL")
    parsed = urlparse(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must be an HTTP or HTTPS URL")
    return normalized


@dataclass(frozen=True)
class ModelCapabilities:
    basic_chat: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    checked_at: datetime | None = None


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
