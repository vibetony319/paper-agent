from __future__ import annotations

from datetime import UTC, datetime

from paper_agent.model_profile_storage import ModelProfileRepository
from paper_agent.model_profiles import ModelCapabilities
from paper_agent.services.reasoning_clients import ReasoningClientProvider


_BASIC_MESSAGES = [{"role": "user", "content": "Return exactly OK."}]
_STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "ok"}},
    "required": ["status"],
    "additionalProperties": False,
}


class ModelProfileService:
    def __init__(
        self, repository: ModelProfileRepository, provider: ReasoningClientProvider
    ) -> None:
        self.repository = repository
        self.provider = provider

    def test_capabilities(self, profile_id: str) -> ModelCapabilities:
        resolved = self.provider.resolve(profile_id)
        basic_chat = self._test_basic_chat(resolved.chat)
        structured_output = False
        tool_calling = False
        if basic_chat:
            structured_output = self._test_structured_output(resolved.structured)
            tool_calling = self._test_tool_calling(resolved.tools)
        capabilities = ModelCapabilities(
            basic_chat=basic_chat,
            structured_output=structured_output,
            tool_calling=tool_calling,
            checked_at=datetime.now(UTC),
        )
        if self.provider.is_read_only_profile(resolved.profile.id):
            return capabilities
        return self.repository.update_capabilities(
            resolved.profile.id,
            expected_revision=resolved.profile.revision,
            capabilities=capabilities,
        ).capabilities

    @staticmethod
    def _test_basic_chat(chat: object) -> bool:
        try:
            return chat.complete(_BASIC_MESSAGES) == "OK"
        except Exception:
            return False

    @staticmethod
    def _test_structured_output(structured: object) -> bool:
        try:
            return structured.generate_json(
                system_prompt="Return the requested JSON object.",
                user_prompt="Return the status object.",
                schema_name="capability_status",
                schema=_STRUCTURED_SCHEMA,
            ) == {"status": "ok"}
        except Exception:
            return False

    @staticmethod
    def _test_tool_calling(tools: object) -> bool:
        try:
            tools.validate_tool_calling()
            return True
        except Exception:
            return False
