from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient


def _upload(client: TestClient, sample_pdf: Path) -> dict:
    response = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", sample_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()


def _highlight_payload(
    request_id: str | None = None,
    *,
    quote: str = "selected text",
    x0: float = 0.1,
    x1: float = 0.8,
) -> dict:
    return {
        "quote": quote,
        "page_number": 1,
        "rects": [
            {"order": 0, "x0": x0, "y0": 0.2, "x1": x1, "y1": 0.25}
        ],
        "color": "yellow",
        "request_id": request_id or str(uuid4()),
    }


def test_create_highlight_returns_normalized_anchor(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)["id"]

    response = client.post(
        f"/api/papers/{paper_id}/highlights",
        json=_highlight_payload(),
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["anchor"]["page_number"] == 1
    assert payload["anchor"]["quote"] == "selected text"
    assert payload["anchor"]["rects"][0]["order"] == 0


def test_annotation_validation_and_boundary_errors_are_chinese_and_stable(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)["id"]

    invalid_coordinates = client.post(
        f"/api/papers/{paper_id}/highlights",
        json=_highlight_payload(x0=2.0),
    )
    assert invalid_coordinates.status_code == 422

    cross_page = client.post(
        f"/api/papers/{paper_id}/highlights",
        json={
            **_highlight_payload(),
            "rects": [
                {"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25},
                {"order": 2, "x0": 0.1, "y0": 0.3, "x1": 0.8, "y1": 0.35},
            ],
        },
    )
    assert cross_page.status_code == 422
    assert cross_page.json()["code"] == "validation_error"

    missing = client.get("/api/papers/missing/annotations")
    assert missing.status_code == 404
    assert missing.json()["code"] == "annotation_not_found"


def test_highlight_idempotency_and_conflicting_retry(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)["id"]
    request_id = str(uuid4())

    first = client.post(
        f"/api/papers/{paper_id}/highlights",
        json=_highlight_payload(request_id=request_id),
    )
    repeated = client.post(
        f"/api/papers/{paper_id}/highlights",
        json=_highlight_payload(request_id=request_id),
    )
    conflict = client.post(
        f"/api/papers/{paper_id}/highlights",
        json=_highlight_payload(request_id=request_id, quote="different quote"),
    )

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_notes_crud_and_annotation_bundle(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)["id"]
    anchor = {
        "quote": "selected text",
        "page_number": 1,
        "rects": [
            {"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}
        ],
    }

    created = client.post(
        f"/api/papers/{paper_id}/notes",
        json={
            "body": "我的笔记",
            "anchor": anchor,
            "note_type": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert created.status_code == 201
    note = created.json()
    assert note["note_type"] == "manual"
    assert note["ai_generated"] is False
    assert note["anchor_ids"]

    updated = client.patch(
        f"/api/papers/{paper_id}/notes/{note['id']}",
        json={"body": "更新后的笔记", "expected_updated_at": note["updated_at"]},
    )
    assert updated.status_code == 200
    assert updated.json()["body"] == "更新后的笔记"
    assert updated.json()["user_edited"] is True

    bundle = client.get(f"/api/papers/{paper_id}/annotations")
    assert bundle.status_code == 200
    assert [item["id"] for item in bundle.json()["notes"]] == [note["id"]]

    deleted = client.delete(f"/api/papers/{paper_id}/notes/{note['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/papers/{paper_id}/annotations").json()["notes"] == []


def test_client_cannot_write_generated_note_type(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)["id"]

    response = client.post(
        f"/api/papers/{paper_id}/notes",
        json={"body": "伪造生成笔记", "note_type": "explanation"},
    )

    assert response.status_code == 422


def test_delete_highlight_keeps_anchored_note(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)["id"]
    anchor = {
        "quote": "selected text",
        "page_number": 1,
        "rects": [
            {"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}
        ],
    }
    highlight = client.post(
        f"/api/papers/{paper_id}/highlights", json=_highlight_payload()
    ).json()
    note = client.post(
        f"/api/papers/{paper_id}/notes",
        json={"body": "锚点笔记", "anchor": anchor},
    ).json()

    deleted = client.delete(
        f"/api/papers/{paper_id}/highlights/{highlight['id']}"
    )

    assert deleted.status_code == 204
    remaining = client.get(f"/api/papers/{paper_id}/annotations").json()
    assert remaining["highlights"] == []
    assert remaining["notes"][0]["anchor_ids"] == note["anchor_ids"]
