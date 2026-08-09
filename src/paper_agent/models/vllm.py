from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass(frozen=True)
class VllmModelConfig:
    base_url: str
    model: str
    api_key: str = "EMPTY"


class VllmConfigurationError(ValueError):
    """Raised when reasoning model environment settings are incomplete."""


class VllmResponseError(RuntimeError):
    """Raised when vLLM does not provide a valid structured response."""


@dataclass(frozen=True)
class VllmToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class VllmToolTurn:
    content: str | None
    tool_calls: tuple[VllmToolCall, ...]


class VllmToolCallingError(RuntimeError):
    """Raised when vLLM does not provide a valid tool-calling response."""


class VllmStructuredClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None:
        self.config = config
        self.client = client if client is not None else _create_openai_client(config)

    def generate_json(
        self, *, system_prompt: str, user_prompt: str, schema_name: str, schema: dict
    ) -> dict:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    },
                },
            )
            result = json.loads(response.choices[0].message.content)
        except Exception as error:
            raise VllmResponseError("vLLM returned an invalid structured response.") from error
        if not isinstance(result, dict):
            raise VllmResponseError("vLLM structured response must be a JSON object.")
        return result


class VllmToolCallingClient:
    def __init__(self, config: VllmModelConfig, client: OpenAI | None = None) -> None:
        self.config = config
        try:
            self.client = client if client is not None else _create_openai_client(config)
        except Exception:
            raise VllmToolCallingError("vLLM tool client initialization failed.") from None

    def request_tool_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: tuple[dict[str, object]],
        tool_choice: str | dict[str, object],
    ) -> VllmToolTurn:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=list(tools),
                tool_choice=tool_choice,
                parallel_tool_calls=False,
                temperature=0,
            )
            message = response.choices[0].message
            content = message.content
            if content is not None and not isinstance(content, str):
                raise TypeError
            raw_tool_calls = message.tool_calls
            if raw_tool_calls is None:
                raw_tool_calls = ()
            tool_calls = tuple(self._parse_tool_call(tool_call) for tool_call in raw_tool_calls)
        except VllmToolCallingError:
            raise
        except Exception:
            raise VllmToolCallingError("vLLM returned an invalid tool response.") from None
        return VllmToolTurn(content=content, tool_calls=tool_calls)

    def generate_json_messages(
        self,
        *,
        messages: list[dict[str, object]],
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    },
                },
            )
            result = json.loads(response.choices[0].message.content)
            if not isinstance(result, dict):
                raise TypeError
        except Exception:
            raise VllmToolCallingError("vLLM returned an invalid final JSON response.") from None
        return result

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
        try:
            turn = self.request_tool_turn(
                messages=[
                    {
                        "role": "system",
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
        except VllmToolCallingError:
            raise
        except Exception:
            raise VllmToolCallingError("vLLM tool calling health validation failed.") from None

    @staticmethod
    def _parse_tool_call(tool_call: object) -> VllmToolCall:
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
            raise VllmToolCallingError("vLLM returned an invalid tool response.") from None
        return VllmToolCall(id=call_id, name=name, arguments=arguments)


def _create_openai_client(config: VllmModelConfig) -> OpenAI:
    from openai import OpenAI

    return OpenAI(base_url=config.base_url, api_key=config.api_key)
