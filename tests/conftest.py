from pathlib import Path

import pymupdf
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


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "Introduction")
    document.save(path)
    document.close()
    return path


@pytest.fixture
def encrypted_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "encrypted.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 20), "Locked content")
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    document.close()
    return path


@pytest.fixture
def visual_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "visuals.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    pixmap = pymupdf.Pixmap(
        pymupdf.csRGB, 10, 10, bytes([0, 0, 0] * 100), False
    )
    page.insert_image(pymupdf.Rect(20, 30, 60, 70), pixmap=pixmap)
    page.draw_rect(pymupdf.Rect(100, 120, 160, 180))
    document.save(path)
    document.close()
    return path


@pytest.fixture
def visual_pdf_with_empty_drawings(tmp_path: Path) -> Path:
    path = tmp_path / "empty-visuals.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.draw_rect(pymupdf.Rect(20, 30, 60, 70))
    page.draw_rect(pymupdf.Rect(100, 120, 100, 180))
    page.draw_rect(pymupdf.Rect(220, 120, 260, 180))
    document.save(path)
    document.close()
    return path
