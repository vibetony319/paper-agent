from dataclasses import dataclass
import os
from pathlib import Path

from paper_agent.database import database_url_for
from paper_agent.models.vllm import VllmConfigurationError, VllmModelConfig


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    reasoning_model: VllmModelConfig | None = None

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "papers").mkdir(exist_ok=True)

        if self.database_url.startswith("sqlite:///"):
            database_path = Path(self.database_url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)


def get_settings(*, data_dir: Path | None = None) -> Settings:
    resolved_data_dir = data_dir or Path.cwd() / ".paper-agent"
    return Settings(
        data_dir=resolved_data_dir,
        database_url=database_url_for(resolved_data_dir),
        reasoning_model=_reasoning_model_config(),
    )


def _reasoning_model_config() -> VllmModelConfig | None:
    base_url = os.getenv("PAPER_AGENT_REASONING_BASE_URL")
    model = os.getenv("PAPER_AGENT_REASONING_MODEL")
    if (base_url is None) != (model is None):
        raise VllmConfigurationError(
            "PAPER_AGENT_REASONING_BASE_URL and PAPER_AGENT_REASONING_MODEL must both be set."
        )
    if base_url is None:
        return None
    return VllmModelConfig(
        base_url=base_url,
        model=model,
        api_key=os.getenv("PAPER_AGENT_REASONING_API_KEY", "EMPTY"),
    )
