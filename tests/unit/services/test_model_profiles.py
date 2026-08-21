from datetime import UTC, datetime
from pathlib import Path

import pytest

from paper_agent.model_profile_storage import ModelProfileRepository
from paper_agent.model_profiles import ModelCapabilities, ModelProfile
from paper_agent.services.model_profiles import ModelProfileService
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID


class ProbeChat:
    def __init__(self, response: str | Exception) -> None:
        self.response = response

    def complete(self, _messages: list[dict[str, str]]) -> str:
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
