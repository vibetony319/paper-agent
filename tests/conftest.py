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
    page.insert_text((20, 20), "Sample body text.")
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


@pytest.fixture
def rotated_cropped_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "rotated-cropped.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=300)
    page.set_cropbox(pymupdf.Rect(50, 30, 350, 260))
    page.insert_text((100, 100), "Crop rotated text")
    page.draw_rect(pymupdf.Rect(250, 180, 300, 220))
    page.set_rotation(90)
    document.save(path)
    document.close()
    return path


@pytest.fixture
def table_pdf(tmp_path: Path) -> Path:
    """A ruled 2x3 grid with a "Table 1" caption above it."""
    path = tmp_path / "table.pdf"
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.insert_text((40, 84), "Table 1: Expert utilization", fontsize=10)
    cells = [
        ["Expert", "Tokens"],
        ["FFN-A", "12.4"],
        ["FFN-B", "9.8"],
    ]
    for row in range(3):
        for column in range(2):
            rect = pymupdf.Rect(
                40 + column * 60,
                100 + row * 20,
                40 + (column + 1) * 60,
                100 + (row + 1) * 20,
            )
            page.draw_rect(rect, color=(0, 0, 0), width=0.5)
            page.insert_text((rect.x0 + 6, rect.y0 + 14), cells[row][column], fontsize=10)
    document.save(path)
    document.close()
    return path


@pytest.fixture
def hyphenated_pdf(tmp_path: Path) -> Path:
    """Body text with one word hyphen-broken across two rendered lines."""
    path = tmp_path / "hyphenated.pdf"
    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    page.insert_text((40, 100), "The model scales its capa-", fontsize=10)
    page.insert_text((40, 115), "bilities across routed experts.", fontsize=10)
    document.save(path)
    document.close()
    return path
