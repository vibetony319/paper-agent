import json
from pathlib import Path

from fastapi.testclient import TestClient

from paper_agent.domain import ProcessingStatus


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


def test_page_image_invalid_page_paths_are_not_found_after_upload(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if invalid page paths bypass the page-image not-found contract."""
    paper_id = _upload_pdf(client, sample_pdf)["id"]

    for page_number in ("0", "-1", "2", "not-a-page"):
        response = client.get(f"/api/papers/{paper_id}/pages/{page_number}/image")

        assert response.status_code == 404


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


def test_notes_accept_same_paper_page_and_reject_nonexistent_page(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if page-targeted notes do not enforce paper page ownership."""
    paper_id = _upload_pdf(client, sample_pdf)["id"]

    created = client.post(
        f"/api/papers/{paper_id}/notes",
        json={"body": "Review this page.", "page_number": 1},
    )
    invalid_page = client.post(
        f"/api/papers/{paper_id}/notes",
        json={"body": "This page does not exist.", "page_number": 2},
    )

    assert created.status_code == 201
    assert created.json()["page_number"] == 1
    assert client.get(f"/api/papers/{paper_id}/notes").json() == [created.json()]
    assert invalid_page.status_code == 422


def test_paper_summary_maps_unknown_persisted_error_to_safe_message(
    client: TestClient,
) -> None:
    """Breaks if public summaries expose arbitrary persisted processing errors."""
    repository = client.app.state.paper_ingestion_service.repository
    paper = repository.create_paper(
        original_filename="failed.pdf",
        stored_filename="failed.pdf",
        status=ProcessingStatus.failed,
    )
    unsafe_error = "Conversion failed for C:\\Users\\Admin\\secret.pdf"
    repository.record_processing_status(
        paper.id, ProcessingStatus.failed, error_summary=unsafe_error
    )

    response = client.get(f"/api/papers/{paper.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "The paper could not be processed."
    assert unsafe_error not in response.text
    assert "C:\\Users\\Admin\\secret.pdf" not in response.text
