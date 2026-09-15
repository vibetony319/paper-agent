from types import SimpleNamespace

import pytest

from paper_agent.config import get_settings
from paper_agent.models.vllm import (
    VllmChatClient,
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


def _exception_chain_text(error: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return "\n".join(messages)


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

    assert secret not in _exception_chain_text(caught.value)


def test_chat_client_completes_only_the_first_nonempty_text_choice(fake_openai_client):
    """Breaks if normal text completion accepts empty or non-text provider output."""
    fake_openai_client.response = _response("OK", "ignored")

    result = VllmChatClient(_config(), client=fake_openai_client).complete(
        [{"role": "user", "content": "health check"}]
    )

    assert result == "OK"
    assert fake_openai_client.requests == [
        {
            "model": "qwen-test",
            "messages": [{"role": "user", "content": "health check"}],
            "temperature": 0,
        }
    ]


@pytest.mark.parametrize(
    "response",
    [
        _response(""),
        _response(None),
        SimpleNamespace(choices=[]),
    ],
)
def test_chat_client_hides_invalid_completion_payloads(response):
    """Breaks if provider completion payload text leaks from a public client error."""
    client = VllmChatClient(
        _config(), client=FakeOpenAIClient(response=response, error=None)
    )

    with pytest.raises(VllmResponseError, match="could not complete text") as caught:
        client.complete([{"role": "user", "content": "prompt-secret"}])

    assert "prompt-secret" not in str(caught.value)


def test_chat_client_hides_provider_errors_from_the_complete_exception_chain():
    """Breaks if a public chat failure retains raw provider text in its exception chain."""
    client = VllmChatClient(
        _config(),
        client=FakeOpenAIClient(error=RuntimeError("complete-provider-secret")),
    )

    with pytest.raises(VllmResponseError) as caught:
        client.complete([{"role": "user", "content": "prompt-secret"}])

    assert "complete-provider-secret" not in _exception_chain_text(caught.value)
    assert "prompt-secret" not in _exception_chain_text(caught.value)


@pytest.mark.parametrize(
    ("client_class", "message"),
    [
        (VllmChatClient, "chat client initialization failed"),
        (VllmStructuredClient, "structured client initialization failed"),
    ],
)
def test_text_clients_hide_transport_construction_failures_from_exception_chains(
    monkeypatch, client_class, message
):
    """Breaks if direct client construction leaks a raw transport configuration failure."""
    import paper_agent.models.vllm as vllm_module

    def fail_client_creation(_config):
        raise RuntimeError("transport-construction-secret")

    monkeypatch.setattr(vllm_module, "_create_openai_client", fail_client_creation)

    with pytest.raises(VllmResponseError, match=message) as caught:
        client_class(_config())

    assert "transport-construction-secret" not in _exception_chain_text(caught.value)


def test_chat_stream_yields_text_deltas_and_skips_empty_deltas(fake_openai_client):
    """Breaks if stream responses are not requested or valid text deltas are lost."""
    fake_openai_client.response = iter(
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="one"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" two"))]),
        ]
    )

    result = list(
        VllmChatClient(_config(), client=fake_openai_client).stream_text(
            [{"role": "user", "content": "explain"}]
        )
    )

    assert result == ["one", " two"]
    assert fake_openai_client.requests[0]["stream"] is True


def test_chat_stream_skips_usage_only_chunks(fake_openai_client):
    """Breaks if a bookkeeping chunk aborts a stream that already produced text."""
    fake_openai_client.response = iter(
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="one"))]),
            SimpleNamespace(choices=[]),
            SimpleNamespace(),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" two"))]),
        ]
    )

    result = list(
        VllmChatClient(_config(), client=fake_openai_client).stream_text(
            [{"role": "user", "content": "explain"}]
        )
    )

    assert result == ["one", " two"]


def test_chat_stream_rejects_non_text_deltas(fake_openai_client):
    """Breaks if malformed streaming deltas are forwarded as text."""
    fake_openai_client.response = iter([object()])
    client = VllmChatClient(_config(), client=fake_openai_client)

    with pytest.raises(VllmResponseError, match="could not stream text"):
        list(client.stream_text([{"role": "user", "content": "explain"}]))


def test_chat_stream_hides_creation_failures_from_the_complete_exception_chain():
    """Breaks if stream creation exposes raw provider details through exception context."""
    client = VllmChatClient(
        _config(), client=FakeOpenAIClient(error=RuntimeError("stream-create-secret"))
    )

    with pytest.raises(VllmResponseError) as caught:
        list(client.stream_text([{"role": "user", "content": "prompt-secret"}]))

    assert "stream-create-secret" not in _exception_chain_text(caught.value)
    assert "prompt-secret" not in _exception_chain_text(caught.value)


def test_chat_stream_hides_late_iterator_failures_after_text_was_yielded():
    """Breaks if a provider failure after a valid delta leaks through stream context."""
    def chunks():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="first"))]
        )
        raise RuntimeError("late-stream-secret")

    client = VllmChatClient(_config(), client=FakeOpenAIClient(response=chunks()))
    stream = client.stream_text([{"role": "user", "content": "prompt-secret"}])

    assert next(stream) == "first"
    with pytest.raises(VllmResponseError) as caught:
        next(stream)

    assert "late-stream-secret" not in _exception_chain_text(caught.value)
    assert "prompt-secret" not in _exception_chain_text(caught.value)


@pytest.mark.parametrize(
    "stream",
    [
        iter(()),
        iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])]),
        iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""))])]),
    ],
)
def test_chat_stream_rejects_streams_without_usable_text(stream):
    """Breaks if an empty or no-content stream is reported as a successful response."""
    client = VllmChatClient(_config(), client=FakeOpenAIClient(response=stream))

    with pytest.raises(VllmResponseError, match="could not stream text"):
        list(client.stream_text([{"role": "user", "content": "explain"}]))


@pytest.fixture
def fake_openai_client() -> FakeOpenAIClient:
    return FakeOpenAIClient(response=_response('{"nodes": []}', "not valid json"))
