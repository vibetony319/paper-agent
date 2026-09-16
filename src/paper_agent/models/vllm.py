from __future__ import annotations

from dataclasses import dataclass
import json
from jsonschema import Draft202012Validator
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass(frozen=True)
class VllmModelConfig:
    base_url: str
    model: str
    api_key: str = "EMPTY"
    context_length: int | None = None
    max_output_tokens: int | None = None


class VllmConfigurationError(ValueError):
    """Raised when reasoning model environment settings are incomplete."""


class VllmResponseError(RuntimeError):
    """Raised when vLLM does not provide a valid structured response."""


def _optional_max_tokens(max_output_tokens: int | None) -> dict[str, int]:
    """Providers keep their own output cap when max_tokens is omitted."""
    return {} if max_output_tokens is None else {"max_tokens": max_output_tokens}


def _balanced_object(content: str, start: int) -> str | None:
    """Return the brace-balanced object text starting at a '{', or None."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return content[start:index + 1]
    return None


def _extract_json_object(content: object, schema: dict[str, object]) -> dict | None:
    """Parse the first schema-valid JSON object embedded in model output.

    Some chat models answer in prose before emitting the requested JSON even
    under json_object mode, so a strict whole-string parse would reject an
    otherwise valid payload.
    """
    if not isinstance(content, str):
        return None
    validator = Draft202012Validator(schema)
    first_parsed: dict | None = None
    start = content.find("{")
    while start != -1:
        candidate = _balanced_object(content, start)
        if candidate is not None:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                if first_parsed is None:
                    first_parsed = parsed
                if not any(validator.iter_errors(parsed)):
                    return parsed
        start = content.find("{", start + 1)
    return first_parsed


def _json_completion(client, *, model, messages, schema_name, schema, max_output_tokens=None):
    """Prefer native schema mode; only negotiate on explicit unsupported-format errors."""
    from openai import BadRequestError

    try:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0,
            response_format={"type": "json_schema", "json_schema": {
                "name": schema_name, "schema": schema, "strict": True,
            }},
            **_optional_max_tokens(max_output_tokens),
        )
    except BadRequestError as error:
        detail = json.dumps(error.body, ensure_ascii=False).lower()
        if "json_schema" not in detail or not any(
            term in detail for term in ("not supported", "unsupported", "does not support")
        ):
            raise
        # Keep every source/tool message. The local schema remains the transport
        # boundary even when the provider only guarantees JSON syntax; it does
        # not judge whether the model's answer is substantively correct.
        response = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content":
                       "Return only a JSON object matching this JSON Schema: " + json.dumps(schema)}] + list(messages),
            response_format={"type": "json_object"},
            **_optional_max_tokens(max_output_tokens),
        )
    result = _extract_json_object(response.choices[0].message.content, schema)
    if result is None:
        raise ValueError("Expected a JSON object")
    Draft202012Validator(schema).validate(result)
    return result


def _plain_completion(client, *, model: str, messages: list, max_output_tokens=None) -> str:
    """Return the assistant text of one non-streaming completion."""
    response = client.chat.completions.create(
        model=model, messages=messages, temperature=0,
        **_optional_max_tokens(max_output_tokens),
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise TypeError("completion has no text content")
    return content


def _streamed_completion(client, *, model: str, messages: list, max_output_tokens=None) -> Iterator[str]:
    """Yield the text deltas of one streaming completion."""
    stream = client.chat.completions.create(
        model=model, messages=messages, temperature=0, stream=True,
        **_optional_max_tokens(max_output_tokens),
    )
    yielded_text = False
    for chunk in stream:
        # OpenAI-compatible providers close a stream with a usage-only chunk
        # that carries no choices; it is bookkeeping, not answer text.
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta.content
        if delta is None or delta == "":
            continue
        if not isinstance(delta, str):
            raise TypeError("stream delta is not text")
        yielded_text = True
        yield delta
    if not yielded_text:
        raise TypeError("stream produced no text")


class VllmChatClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None:
        self.config = config
        if client is not None:
            self.client = client
            return
        failed = False
        transport: OpenAI | None = None
        try:
            transport = _create_openai_client(config)
        except Exception:
            failed = True
        if failed:
            raise VllmResponseError("vLLM chat client initialization failed.")
        self.client = transport

    def complete(self, messages: list[dict[str, str]]) -> str:
        failed = False
        content: str = ""
        try:
            content = _plain_completion(
                self.client, model=self.config.model, messages=messages,
                max_output_tokens=self.config.max_output_tokens,
            )
        except Exception:
            failed = True
        if failed:
            raise VllmResponseError("vLLM could not complete text generation.")
        return content

    def stream_text(self, messages: list[dict[str, str]]) -> Iterator[str]:
        # Provider text must not reach the exception chain, so the error is
        # raised after the except block instead of being chained to it.
        failed = False
        try:
            for delta in _streamed_completion(
                self.client, model=self.config.model, messages=messages,
                max_output_tokens=self.config.max_output_tokens,
            ):
                yield delta
        except Exception:
            failed = True
        if failed:
            raise VllmResponseError("vLLM could not stream text generation.")


@dataclass(frozen=True)
class VllmToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class VllmToolTurn:
    content: str | None
    tool_calls: tuple[VllmToolCall, ...]
    # Thinking-mode providers return their reasoning alongside the tool call and
    # reject a follow-up request that replays the turn without it.
    reasoning_content: str | None = None


class VllmToolCallingError(RuntimeError):
    """Raised when vLLM does not provide a valid tool-calling response."""


class VllmStructuredClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None:
        self.config = config
        if client is not None:
            self.client = client
            return
        failed = False
        transport: OpenAI | None = None
        try:
            transport = _create_openai_client(config)
        except Exception:
            failed = True
        if failed:
            raise VllmResponseError("vLLM structured client initialization failed.")
        self.client = transport

    def generate_json(
        self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict
    ) -> dict:
        failed = False
        result: object = None
        try:
            result = _json_completion(
                self.client, model=self.config.model,
                schema_name=schema_name, schema=schema, messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_output_tokens=self.config.max_output_tokens,
            )
        except Exception:
            failed = True
        if failed:
            raise VllmResponseError("vLLM returned an invalid structured response.")
        if not isinstance(result, dict):
            raise VllmResponseError("vLLM structured response must be a JSON object.")
        return result


class VllmToolCallingClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None:
        self.config = config
        failed = False
        try:
            self.client = client if client is not None else _create_openai_client(config)
        except Exception:
            failed = True
        if failed:
            raise VllmToolCallingError("vLLM tool client initialization failed.")

    def request_tool_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: tuple[dict[str, object]],
        tool_choice: str | dict[str, object],
    ) -> VllmToolTurn:
        failed = False
        content: str | None = None
        reasoning_content: str | None = None
        tool_calls: tuple[VllmToolCall, ...] = ()
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=list(tools),
                tool_choice=tool_choice,
                parallel_tool_calls=False,
                temperature=0,
                **_optional_max_tokens(self.config.max_output_tokens),
            )
            message = response.choices[0].message
            content = message.content
            if content is not None and not isinstance(content, str):
                raise TypeError
            raw_reasoning = getattr(message, "reasoning_content", None)
            if raw_reasoning is not None and not isinstance(raw_reasoning, str):
                raise TypeError
            reasoning_content = raw_reasoning
            raw_tool_calls = message.tool_calls
            if raw_tool_calls is None:
                raw_tool_calls = ()
            tool_calls = tuple(self._parse_tool_call(tool_call) for tool_call in raw_tool_calls)
        except Exception:
            failed = True
        if failed:
            raise VllmToolCallingError("vLLM returned an invalid tool response.")
        return VllmToolTurn(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )

    def complete_markdown_messages(self, *, messages: list[dict[str, object]]) -> str:
        """Return the final answer text of a turn that is already done with tools."""
        failed = False
        content = ""
        try:
            content = _plain_completion(
                self.client, model=self.config.model, messages=messages,
                max_output_tokens=self.config.max_output_tokens,
            )
        except Exception:
            failed = True
        if failed:
            raise VllmToolCallingError("vLLM returned an invalid final answer.")
        return content

    def stream_final_answer(
        self, *, messages: list[dict[str, object]]
    ) -> Iterator[str]:
        """Stream the final Markdown answer of a turn that is done with tools."""
        failed = False
        try:
            for delta in _streamed_completion(
                self.client, model=self.config.model, messages=messages,
                max_output_tokens=self.config.max_output_tokens,
            ):
                yield delta
        except Exception:
            failed = True
        if failed:
            raise VllmToolCallingError("vLLM could not stream the final answer.")

    def validate_tool_calling(self) -> None:
        health_tool = {
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
        tool_error: VllmToolCallingError | None = None
        failed = False
        try:
            turn = self.request_tool_turn(
                messages=[
                    {
                        "role": "user",
                        "content": "Call paper_agent_tool_health with no arguments.",
                    }
                ],
                tools=(health_tool,),
                tool_choice="auto",
            )
            if (
                len(turn.tool_calls) != 1
                or turn.tool_calls[0].name != "paper_agent_tool_health"
                or turn.tool_calls[0].arguments != {}
            ):
                raise VllmToolCallingError("vLLM tool calling health validation failed.")
        except VllmToolCallingError as error:
            tool_error = error
        except Exception:
            failed = True
        if tool_error is not None:
            raise tool_error
        if failed:
            raise VllmToolCallingError("vLLM tool calling health validation failed.")

    @staticmethod
    def _parse_tool_call(tool_call: object) -> VllmToolCall:
        failed = False
        call_id: object = None
        name: object = None
        arguments: object = None
        try:
            call_id = getattr(tool_call, "id")
            function = getattr(tool_call, "function")
            name = getattr(function, "name")
            raw_arguments = getattr(function, "arguments")
            arguments = json.loads(raw_arguments)
            if (
                not isinstance(call_id, str)
                or not isinstance(name, str)
                or not isinstance(arguments, dict)
            ):
                raise TypeError
        except Exception:
            failed = True
        if failed:
            raise VllmToolCallingError("vLLM returned an invalid tool response.")
        return VllmToolCall(id=call_id, name=name, arguments=arguments)


def _create_openai_client(config: VllmModelConfig) -> OpenAI:
    from openai import OpenAI

    return OpenAI(base_url=config.base_url, api_key=config.api_key)
