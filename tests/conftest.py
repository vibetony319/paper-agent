from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paper_agent.app import create_app
from paper_agent.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'paper-agent.db'}",
    )
    return TestClient(create_app(settings))
