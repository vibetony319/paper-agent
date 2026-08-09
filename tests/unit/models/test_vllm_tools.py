from types import SimpleNamespace

import pytest

from paper_agent.models import (
    VllmModelConfig,
    VllmToolCallingClient,
    VllmToolCallingError,
)


class FakeOpenAI:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.error: Exception | None = None
        self.tool_arguments = '{"element_id": "e1"}'
        self.tool_name = "read_element"
        self.tool_id = "call-1"
        self.content: str | None = "I will read it."
        self.json_content = '{"answer": "ok"}'
        self.empty_choices = False
        self.chat = SimpleNamespace(completions=self)

    def set_tool_arguments(self, arguments: str) -> None:
        self.tool_arguments = arguments

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.empty_choices:
            return SimpleNamespace(choices=[])
        if "response_format" in kwargs:
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.json_content))]
            )
        tool_call = SimpleNamespace(
            id=self.tool_id,
            function=SimpleNamespace(name=self.tool_name, arguments=self.tool_arguments),
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content, tool_calls=[tool_call])
                ),
                SimpleNamespace(message=SimpleNamespace(content="ignore", tool_calls=[])),
            ]
        )


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


def _config() -> VllmModelConfig:
    return VllmModelConfig(
        base_url="http://127.0.0.1:8000/v1", model="qwen-test", api_key="test-key"
    )


def _read_element_tool() -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "read_element",
            "description": "Read an evidence element.",
            "parameters": {
                "type": "object",
                "properties": {"element_id": {"type": "string"}},
                "required": ["element_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def test_tool_turn_uses_single_nonparallel_call_and_parses_arguments(fake_openai):
    """Breaks if a request allows parallel calls or does not decode tool arguments."""
    client = VllmToolCallingClient(_config(), client=fake_openai)

    turn = client.request_tool_turn(
        messages=[{"role": "user", "content": "Read the method."}],
        tools=(_read_element_tool(),),
        tool_choice="auto",
    )

    assert turn.content == "I will read it."
    assert turn.tool_calls[0].id == "call-1"
    assert turn.tool_calls[0].name == "read_element"
    assert turn.tool_calls[0].arguments == {"element_id": "e1"}
    assert fake_openai.requests == [
        {
            "model": "qwen-test",
            "messages": [{"role": "user", "content": "Read the method."}],
            "tools": [_read_element_tool()],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "temperature": 0,
        }
    ]


def test_tool_client_never_exposes_raw_model_payload_on_bad_arguments(fake_openai):
    """Breaks if malformed model tool arguments leak through the public error."""
    fake_openai.set_tool_arguments("{bad-secret-from-server")

    with pytest.raises(VllmToolCallingError) as error:
        VllmToolCallingClient(_config(), client=fake_openai).request_tool_turn(
            messages=[{"role": "user", "content": "prompt-secret"}],
            tools=(_read_element_tool(),),
            tool_choice="auto",
        )

    assert "bad-secret-from-server" not in str(error.value)
    assert "prompt-secret" not in str(error.value)


def test_tool_client_sanitizes_vllm_tool_calling_errors_from_the_provider(fake_openai):
    """Breaks if a provider can leak sensitive text via the public error type."""
    fake_openai.error = VllmToolCallingError("provider-prompt-secret")

    with pytest.raises(VllmToolCallingError) as error:
        VllmToolCallingClient(_config(), client=fake_openai).request_tool_turn(
            messages=[{"role": "user", "content": "prompt-secret"}],
            tools=(_read_element_tool(),),
            tool_choice="auto",
        )

    assert str(error.value) == "vLLM returned an invalid tool response."
    assert "provider-prompt-secret" not in str(error.value)
    assert "prompt-secret" not in str(error.value)


def test_tool_client_wraps_openai_client_construction_failures_safely(monkeypatch):
    """Breaks if OpenAI client construction exposes its configuration failure."""
    import paper_agent.models.vllm as vllm_module

    def fail_client_creation(_config):
        raise RuntimeError("client-secret")

    monkeypatch.setattr(vllm_module, "_create_openai_client", fail_client_creation)

    with pytest.raises(VllmToolCallingError) as error:
        VllmToolCallingClient(_config())

    assert "client-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("paper_agent_tool_health", "{}"),
        ("wrong_tool", "{}"),
        ("paper_agent_tool_health", '{"unexpected": true}'),
    ],
)
def test_health_check_only_accepts_the_expected_empty_health_call(
    fake_openai, tool_name, arguments
):
    """Breaks if health validation accepts a different tool or any arguments."""
    fake_openai.tool_name = tool_name
    fake_openai.set_tool_arguments(arguments)
    client = VllmToolCallingClient(_config(), client=fake_openai)

    if tool_name == "paper_agent_tool_health" and arguments == "{}":
        client.validate_tool_calling()
    else:
        with pytest.raises(VllmToolCallingError):
            client.validate_tool_calling()

    request = fake_openai.requests[0]
    assert request["tool_choice"] == "auto"
    assert request["parallel_tool_calls"] is False
    assert request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "paper_agent_tool_health",
                "description": "Confirm tool calling is available.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
    ]


def test_generate_json_messages_uses_strict_schema_over_the_full_message_list(fake_openai):
    """Breaks if final JSON omits strict schema mode or reconstructs the messages."""
    messages = [
        {"role": "system", "content": "Use citations."},
        {"role": "user", "content": "What is the conclusion?"},
    ]

    result = VllmToolCallingClient(_config(), client=fake_openai).generate_json_messages(
        messages=messages,
        schema_name="final_answer",
        schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )

    assert result == {"answer": "ok"}
    assert fake_openai.requests == [
        {
            "model": "qwen-test",
            "messages": messages,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "final_answer",
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                    },
                    "strict": True,
                },
            },
        }
    ]


@pytest.mark.parametrize(
    "configure",
    [
        lambda fake: fake.set_tool_arguments('["response-secret"]'),
        lambda fake: setattr(fake, "empty_choices", True),
        lambda fake: setattr(fake, "error", RuntimeError("provider-secret")),
    ],
)
def test_tool_client_wraps_provider_protocol_and_argument_failures_safely(
    fake_openai, configure
):
    """Breaks if any tool request failure exposes provider or prompt data."""
    configure(fake_openai)

    with pytest.raises(VllmToolCallingError) as error:
        VllmToolCallingClient(_config(), client=fake_openai).request_tool_turn(
            messages=[{"role": "user", "content": "prompt-secret"}],
            tools=(_read_element_tool(),),
            tool_choice="auto",
        )

    assert "response-secret" not in str(error.value)
    assert "provider-secret" not in str(error.value)
    assert "prompt-secret" not in str(error.value)
