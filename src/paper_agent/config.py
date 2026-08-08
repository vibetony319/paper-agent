from dataclasses import dataclass
from pathlib import Path

from paper_agent.database import database_url_for


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "papers").mkdir(exist_ok=True)

        if self.database_url.startswith("sqlite:///"):
            database_path = Path(self.database_url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    data_dir = Path.cwd() / ".paper-agent"
    return Settings(data_dir=data_dir, database_url=database_url_for(data_dir))
