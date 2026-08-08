from types import SimpleNamespace

import pytest

from paper_agent.config import get_settings
from paper_agent.models.vllm import (
    VllmConfigurationError,
    VllmModelConfig,
    VllmResponseError,
    VllmStructuredClient,
)


class FakeOpenAIClient:
    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.requests: list[dict] = []
        self.response = response
        self.error = error
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _config() -> VllmModelConfig:
    return VllmModelConfig(
        base_url="http://127.0.0.1:8000/v1", model="qwen-test", api_key="test-key"
    )


def _response(content: str, *additional_contents: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=item))
            for item in (content, *additional_contents)
        ]
    )


@pytest.mark.parametrize(
    "environment",
    [
        {"PAPER_AGENT_REASONING_BASE_URL": "http://127.0.0.1:8000/v1"},
        {"PAPER_AGENT_REASONING_MODEL": "qwen-test"},
    ],
)
def test_settings_rejects_partial_reasoning_vllm_configuration(
    monkeypatch, tmp_path, environment
):
    """Breaks if either required vLLM setting silently enables a partial client."""
    for name in (
        "PAPER_AGENT_REASONING_BASE_URL",
        "PAPER_AGENT_REASONING_MODEL",
        "PAPER_AGENT_REASONING_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(VllmConfigurationError, match="both"):
        get_settings(data_dir=tmp_path / "data")


def test_settings_reads_complete_reasoning_vllm_configuration(monkeypatch, tmp_path):
    """Breaks if a complete reasoning configuration is not exposed to consumers."""
    monkeypatch.setenv("PAPER_AGENT_REASONING_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("PAPER_AGENT_REASONING_MODEL", "qwen-test")
    monkeypatch.setenv("PAPER_AGENT_REASONING_API_KEY", "configured-key")

    settings = get_settings(data_dir=tmp_path / "data")

    assert settings.reasoning_model == VllmModelConfig(
        base_url="http://127.0.0.1:8000/v1", model="qwen-test", api_key="configured-key"
    )


def test_settings_uses_empty_default_api_key_for_complete_reasoning_configuration(
    monkeypatch, tmp_path
):
    """Breaks if local vLLM configuration requires an API key that vLLM does not need."""
    monkeypatch.setenv("PAPER_AGENT_REASONING_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("PAPER_AGENT_REASONING_MODEL", "qwen-test")
    monkeypatch.delenv("PAPER_AGENT_REASONING_API_KEY", raising=False)

    settings = get_settings(data_dir=tmp_path / "data")

    assert settings.reasoning_model == VllmModelConfig(
        base_url="http://127.0.0.1:8000/v1", model="qwen-test"
    )


def test_structured_client_returns_only_the_first_json_object(fake_openai_client):
    """Breaks if requests omit the schema or responses use anything beyond choice zero."""
    client = VllmStructuredClient(_config(), client=fake_openai_client)

    result = client.generate_json(
        system_prompt="extract",
        user_prompt="source",
        schema_name="nodes",
        schema={"type": "object"},
    )

    assert result == {"nodes": []}
    assert fake_openai_client.requests == [
        {
            "model": "qwen-test",
            "messages": [
                {"role": "system", "content": "extract"},
                {"role": "user", "content": "source"},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "nodes",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            },
        }
    ]


@pytest.mark.parametrize(
    ("response", "error", "secret"),
    [
        (_response("not valid json"), None, "not valid json"),
        (_response("[]"), None, "[]"),
        (SimpleNamespace(choices=[]), None, "source"),
        (None, RuntimeError("server response: source"), "source"),
    ],
)
def test_structured_client_wraps_failures_without_exposing_request_or_response_data(
    response, error, secret
):
    """Breaks if provider, protocol, or JSON failures leak prompts or server output."""
    fake_client = FakeOpenAIClient(response=response, error=error)
    client = VllmStructuredClient(_config(), client=fake_client)

    with pytest.raises(VllmResponseError) as caught:
        client.generate_json(
            system_prompt="extract", user_prompt="source", schema_name="nodes", schema={}
        )

    assert secret not in str(caught.value)


@pytest.fixture
def fake_openai_client() -> FakeOpenAIClient:
    return FakeOpenAIClient(response=_response('{"nodes": []}', "not valid json"))
