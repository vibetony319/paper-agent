"""Unit tests for the paperqa2-backed semantic paper search."""

from pathlib import Path

import pytest

from paper_agent.domain import BoundingBox, DocumentElement, Page, Section
from paper_agent.services.paper_search import SemanticPaperSearchService
from paper_agent.storage import PaperRepository

DIMENSION = 8


class CountingEmbedder:
    """Deterministic keyword embedder that records every call."""

    def __init__(self, keyword_slots: dict[str, int]) -> None:
        self.keyword_slots = keyword_slots
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * DIMENSION
            lowered = text.lower()
            for keyword, slot in self.keyword_slots.items():
                if keyword in lowered:
                    vector[slot] = 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


def _paper_with_elements(repository: PaperRepository, name: str):
    paper = repository.create_paper(
        original_filename=f"{name}.pdf", stored_filename=f"private-{name}.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = repository.save_section(
        paper.id, Section(id=f"{name}-section", title="Body", order=0, page_number=1)
    )
    router = repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{name}-router",
            kind="paragraph",
            text="The router sends tokens onward.",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.2),
            section_id=section.id,
        ),
    )
    experts = repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{name}-experts",
            kind="paragraph",
            text="Experts specialize in different domains.",
            page_number=1,
            bbox=BoundingBox(0, 0.2, 1, 0.4),
            section_id=section.id,
        ),
    )
    conclusion = repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{name}-conclusion",
            kind="paragraph",
            text="We conclude that dispatching improves efficiency.",
            page_number=1,
            bbox=BoundingBox(0, 0.4, 1, 0.6),
            section_id=section.id,
        ),
    )
    return paper, router, experts, conclusion


def test_search_ranks_semantically_matching_elements_first(repository, tmp_path):
    paper, router, experts, _ = _paper_with_elements(repository, "rank")
    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=CountingEmbedder({"router": 0, "expert": 1}),
    )

    hits = service.search(paper.id, "router", limit=2)

    assert [hit.element_id for hit in hits] == [router.id, experts.id]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.0)


def test_index_persists_and_is_reused_by_later_services(repository, tmp_path):
    paper, _, _, _ = _paper_with_elements(repository, "persist")
    index_dir = tmp_path / "indexes"
    first_embedder = CountingEmbedder({"router": 0})
    first = SemanticPaperSearchService(
        repository,
        index_dir=index_dir,
        embedding_model="st-fake",
        embedder=first_embedder,
    )
    assert first.search(paper.id, "router", limit=1)
    # One call embeds the elements, a second embeds the query.
    assert len(first_embedder.calls[0]) == 3
    assert first_embedder.calls[1] == ["router"]

    second_embedder = CountingEmbedder({"router": 0})
    second = SemanticPaperSearchService(
        repository,
        index_dir=index_dir,
        embedding_model="st-fake",
        embedder=second_embedder,
    )

    assert len(second.search(paper.id, "router", limit=1)) == 1
    # Only the query is embedded: the element index came from disk.
    assert second_embedder.calls == [["router"]]


def test_changed_elements_trigger_a_lazy_rebuild(repository, tmp_path):
    paper, _, _, _ = _paper_with_elements(repository, "rebuild")
    embedder = CountingEmbedder({"router": 0, "optimizer": 1})
    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=embedder,
    )
    assert service.search(paper.id, "router", limit=5)

    repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{paper.id}-optimizer",
            kind="paragraph",
            text="The optimizer schedules the experts.",
            page_number=1,
            bbox=BoundingBox(0, 0.6, 1, 0.8),
        ),
    )

    hits = service.search(paper.id, "optimizer", limit=5)
    assert [hit.element_id for hit in hits[:1]] == [f"{paper.id}-optimizer"]
    # The whole index was re-embedded after the content fingerprint changed.
    assert len(embedder.calls[-2]) == 4


def test_index_for_a_different_embedding_model_is_ignored(repository, tmp_path):
    paper, _, _, _ = _paper_with_elements(repository, "model")
    index_dir = tmp_path / "indexes"
    stale = SemanticPaperSearchService(
        repository,
        index_dir=index_dir,
        embedding_model="st-old",
        embedder=CountingEmbedder({"router": 0}),
    )
    assert stale.search(paper.id, "router", limit=1)

    fresh_embedder = CountingEmbedder({"router": 0})
    fresh = SemanticPaperSearchService(
        repository,
        index_dir=index_dir,
        embedding_model="st-new",
        embedder=fresh_embedder,
    )

    assert fresh.search(paper.id, "router", limit=1)
    # The sidecar records the old model, so the elements are re-embedded.
    assert len(fresh_embedder.calls[0]) == 3


def test_failing_embedder_yields_no_hits(repository, tmp_path):
    paper, _, _, _ = _paper_with_elements(repository, "failing")

    def exploding(_texts):
        raise RuntimeError("embedding backend down")

    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=exploding,
    )

    assert service.search(paper.id, "router", limit=5) == ()


def test_paper_without_embeddable_text_yields_no_hits(repository, tmp_path):
    paper = repository.create_paper(
        original_filename="empty.pdf", stored_filename="private-empty.pdf"
    )
    repository.save_element(
        paper.id,
        DocumentElement.paragraph("   ", location_status="unlocated"),
    )
    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=CountingEmbedder({"router": 0}),
    )

    assert service.search(paper.id, "router", limit=5) == ()
