import json
from pathlib import Path

from fastapi.testclient import TestClient


def _upload_pdf(client: TestClient, sample_pdf: Path, filename: str = "paper.pdf") -> dict:
    response = client.post(
        "/api/papers",
        files={"file": (filename, sample_pdf.read_bytes(), "application/pdf")},
    )

    assert response.status_code == 201
    return response.json()


def test_upload_then_retrieve_summary_document_source_and_page_image(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if the local paper API does not serve an ingested source PDF."""
    uploaded = _upload_pdf(client, sample_pdf)
    paper_id = uploaded["id"]

    summary = client.get(f"/api/papers/{paper_id}")
    document = client.get(f"/api/papers/{paper_id}/document")
    source = client.get(f"/api/papers/{paper_id}/source")
    image = client.get(f"/api/papers/{paper_id}/pages/1/image")

    assert summary.status_code == 200
    assert summary.json() == uploaded
    assert uploaded["original_filename"] == "paper.pdf"
    assert uploaded["status"] == "completed"
    assert document.status_code == 200
    document_body = document.json()
    assert len(document_body["pages"]) == 1
    assert document_body["pages"][0]["number"] == 1
    assert document_body["pages"][0]["width"] == 200.0
    assert document_body["pages"][0]["height"] == 200.0
    assert document_body["elements"]
    assert document_body["notes"] == []
    assert source.status_code == 200
    assert source.headers["content-type"] == "application/pdf"
    assert source.content == sample_pdf.read_bytes()
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_paper_json_responses_do_not_leak_internal_source_paths(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if a public paper DTO exposes service-owned file locations."""
    uploaded = _upload_pdf(client, sample_pdf)
    paper_id = uploaded["id"]
    data_dir = str(client.app.state.settings.data_dir)

    for payload in (
        uploaded,
        client.get(f"/api/papers/{paper_id}").json(),
        client.get(f"/api/papers/{paper_id}/document").json(),
    ):
        serialized = json.dumps(payload)
        assert data_dir not in serialized
        assert "stored_filename" not in serialized
        assert '"path"' not in serialized


def test_invalid_upload_and_unknown_resources_are_mapped_to_safe_http_errors(
    client: TestClient,
) -> None:
    """Breaks if unsafe uploads or missing papers escape the route contract."""
    invalid_upload = client.post(
        "/api/papers",
        files={"file": ("not-a-paper.txt", b"not a PDF", "text/plain")},
    )

    assert invalid_upload.status_code == 422
    assert client.get("/api/papers/missing").status_code == 404
    assert client.get("/api/papers/missing/document").status_code == 404
    assert client.get("/api/papers/missing/source").status_code == 404
    assert client.get("/api/papers/missing/pages/1/image").status_code == 404
    assert client.get("/api/papers/missing/notes").status_code == 404
    assert client.get("/api/papers/missing/pages/0/image").status_code == 404


def test_notes_accept_same_paper_element_and_reject_cross_paper_element(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if notes lose their document ownership boundary."""
    first_paper = _upload_pdf(client, sample_pdf, "first.pdf")
    second_paper = _upload_pdf(client, sample_pdf, "second.pdf")
    first_id = first_paper["id"]
    second_id = second_paper["id"]
    first_element_id = client.get(f"/api/papers/{first_id}/document").json()[
        "elements"
    ][0]["id"]

    created = client.post(
        f"/api/papers/{first_id}/notes",
        json={"body": "Review this evidence.", "element_id": first_element_id},
    )
    cross_paper = client.post(
        f"/api/papers/{second_id}/notes",
        json={"body": "This target belongs elsewhere.", "element_id": first_element_id},
    )

    assert created.status_code == 201
    assert created.json()["body"] == "Review this evidence."
    assert created.json()["element_id"] == first_element_id
    assert created.json()["page_number"] is None
    assert client.get(f"/api/papers/{first_id}/notes").json() == [created.json()]
    assert cross_paper.status_code == 422
