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
    """One paper whose three sections each hold one paragraph.

    Distinct sections keep the three paragraphs in distinct passages so
    ranking, persistence, and rebuild behaviour are observable per passage.
    """
    paper = repository.create_paper(
        original_filename=f"{name}.pdf", stored_filename=f"private-{name}.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))

    def _section(title: str, order: int, element_id: str, text: str):
        repository.save_section(
            paper.id,
            Section(id=f"{name}-s{order}", title=title, order=order, page_number=1),
        )
        return repository.save_element(
            paper.id,
            DocumentElement(
                id=element_id,
                kind="paragraph",
                text=text,
                page_number=1,
                bbox=BoundingBox(0, order * 0.2, 1, order * 0.2 + 0.2),
                section_id=f"{name}-s{order}",
            ),
        )

    router = _section("Router", 0, f"{name}-router", "The router sends tokens onward.")
    experts = _section(
        "Experts", 1, f"{name}-experts", "Experts specialize in different domains."
    )
    conclusion = _section(
        "Conclusion",
        2,
        f"{name}-conclusion",
        "We conclude that dispatching improves efficiency.",
    )
    return paper, router, experts, conclusion


def test_search_ranks_semantically_matching_passages_first(repository, tmp_path):
    paper, router, experts, _ = _paper_with_elements(repository, "rank")
    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=CountingEmbedder({"router": 0, "expert": 1}),
    )

    hits = service.search(paper.id, "router", limit=2)

    # Hits are keyed by passage (the passage's first element id).
    assert [hit.passage_key for hit in hits] == [router.id, experts.id]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.0)


def test_index_embeds_passages_with_their_section_breadcrumbs(repository, tmp_path):
    paper, router, _, _ = _paper_with_elements(repository, "crumb")
    embedder = CountingEmbedder({"router": 0})
    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=embedder,
    )

    assert service.search(paper.id, "router", limit=1)

    # Each embedded text is one passage: breadcrumb header plus body.
    assert embedder.calls[0] == [
        "Router\n\nThe router sends tokens onward.",
        "Experts\n\nExperts specialize in different domains.",
        "Conclusion\n\nWe conclude that dispatching improves efficiency.",
    ]


def test_stage0_elements_are_excluded_from_the_index(repository, tmp_path):
    paper, router, experts, conclusion = _paper_with_elements(repository, "stage0")
    repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{paper.id}-block",
            kind="text_block",
            text="The router sends tokens onward.",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.2),
        ),
    )
    embedder = CountingEmbedder({"router": 0})
    service = SemanticPaperSearchService(
        repository,
        index_dir=tmp_path / "indexes",
        embedding_model="st-fake",
        embedder=embedder,
    )

    hits = service.search(paper.id, "router", limit=5)

    # The stage-0 text block duplicates the stage-1 paragraph and must not
    # occupy index slots: only the three section passages are embedded.
    assert [hit.passage_key for hit in hits] == [router.id, experts.id, conclusion.id]
    assert len(embedder.calls[0]) == 3


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
    # One call embeds the passages, a second embeds the query.
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
    # Only the query is embedded: the passage index came from disk.
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
    assert [hit.passage_key for hit in hits[:1]] == [f"{paper.id}-optimizer"]
    # The whole index was re-embedded after the content fingerprint changed.
    assert len(embedder.calls[-2]) == 4


def test_changed_sections_trigger_a_lazy_rebuild(repository, tmp_path):
    paper, _, _, _ = _paper_with_elements(repository, "resection")
    index_dir = tmp_path / "indexes"
    first_embedder = CountingEmbedder({"router": 0})
    first = SemanticPaperSearchService(
        repository,
        index_dir=index_dir,
        embedding_model="st-fake",
        embedder=first_embedder,
    )
    assert first.search(paper.id, "router", limit=1)

    # A new section changes no element yet, only the section fingerprint
    # (and with it the breadcrumb of every passage below it).
    repository.save_section(
        paper.id,
        Section(id="resection-s3", title="Routing", order=3, page_number=1),
    )

    second_embedder = CountingEmbedder({"router": 0})
    second = SemanticPaperSearchService(
        repository,
        index_dir=index_dir,
        embedding_model="st-fake",
        embedder=second_embedder,
    )

    assert second.search(paper.id, "router", limit=1)
    # The persisted index no longer matches, so the passages re-embed.
    assert len(second_embedder.calls[0]) == 3


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
    # The sidecar records the old model, so the passages are re-embedded.
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
