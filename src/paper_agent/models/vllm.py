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


def _create_openai_client(config: VllmModelConfig) -> OpenAI:
    from openai import OpenAI

    return OpenAI(base_url=config.base_url, api_key=config.api_key)
