from pathlib import Path


def database_url_for(data_dir: Path) -> str:
    return f"sqlite:///{data_dir / 'paper-agent.db'}"
