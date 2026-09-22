"""Semantic paper search backed by paperqa2's local embedding stack.

The service embeds every retrieval passage of a paper once (a passage is
several consecutive same-section elements prefixed with the section
breadcrumb), keeps the vectors on disk, and answers queries by cosine
similarity. Indexes are rebuilt lazily when the paper's element or section
fingerprint changes (re-upload, re-parse), so ingestion never needs an
explicit re-index step. When paperqa2 or the embedding model is unavailable,
``search`` returns no hits and callers fall back to substring matching.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from paper_agent.domain import DocumentElement, Section
from paper_agent.services.passages import build_passages
from paper_agent.storage import PaperRepository

logger = logging.getLogger(__name__)

_INDEX_CACHE_LIMIT = 4

# Bumped whenever the embedded text format changes, so persisted indexes
# from an older format rebuild instead of answering against stale vectors.
_INDEX_FORMAT_VERSION = "passages-v1"


class EmbeddingFunction(Protocol):
    def __call__(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class SearchHit:
    passage_key: str
    score: float


@dataclass(frozen=True)
class _PaperIndex:
    passage_keys: tuple[str, ...]
    matrix: np.ndarray
    fingerprint: str


def _content_fingerprint(
    elements: tuple[DocumentElement, ...], sections: tuple[Section, ...]
) -> str:
    digest = hashlib.sha256()
    digest.update(_INDEX_FORMAT_VERSION.encode("utf-8"))
    digest.update(b"\x1e")
    for section in sorted(sections, key=lambda item: item.order):
        digest.update(section.id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(section.order).encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(section.level).encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(section.title.encode("utf-8"))
        digest.update(b"\x1e")
    for element in elements:
        digest.update(element.id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(element.order).encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(element.kind.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(element.text.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _paperqa_embedder(model_name: str) -> EmbeddingFunction | None:
    """Wrap paperqa2's local embedding stack, or return None when unusable."""
    try:
        from paperqa import embedding_model_factory

        model = embedding_model_factory(model_name)

        def embed(texts: list[str]) -> list[list[float]]:
            return asyncio.run(model.embed_documents(texts))

        return embed
    except Exception as error:
        logger.warning(
            "paperqa2 embedding model %r is unavailable (%s); "
            "search_paper falls back to substring matching.",
            model_name,
            error,
        )
        return None


class SemanticPaperSearchService:
    """Cosine top-k search over per-passage embeddings with disk persistence."""

    def __init__(
        self,
        repository: PaperRepository,
        *,
        index_dir: Path,
        embedding_model: str,
        embedder: EmbeddingFunction | None = None,
    ) -> None:
        self.repository = repository
        self.index_dir = Path(index_dir)
        self.embedding_model = embedding_model
        self._embedder_override = embedder
        self._embedder: EmbeddingFunction | None | None = None
        self._embedder_resolved = embedder is not None
        if embedder is not None:
            self._embedder = embedder
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, _PaperIndex] = OrderedDict()

    def search(
        self, paper_id: str, query: str, *, limit: int = 10
    ) -> tuple[SearchHit, ...]:
        """Return up to ``limit`` semantic hits, best match first.

        Hits are keyed by passage (the passage's first element id); callers
        rebuild the same passages to resolve the key into text.

        Embedding and index building are serialized by one lock: the
        underlying model is not safe for concurrent encode calls.
        """
        with self._lock:
            index = self._index_for(paper_id)
            embedder = self._resolve_embedder()
            if index is None or embedder is None or index.matrix.shape[0] == 0:
                return ()
            try:
                vector = np.asarray(embedder([query]), dtype=np.float32)
            except Exception as error:
                logger.warning(
                    "embedding the search query for paper %s failed (%s); "
                    "returning no semantic hits.",
                    paper_id,
                    error,
                )
                return ()
            if vector.ndim != 2 or vector.shape[0] != 1:
                return ()
            norm = float(np.linalg.norm(vector[0]))
            if norm == 0.0 or vector.shape[1] != index.matrix.shape[1]:
                return ()
            scores = index.matrix @ (vector[0] / norm)
            count = min(limit, scores.shape[0])
            if count <= 0:
                return ()
            # Stable sort keeps document order among equally-scored hits.
            best = np.argsort(-scores, kind="stable")[:count]
            return tuple(
                SearchHit(
                    passage_key=index.passage_keys[position],
                    score=float(scores[position]),
                )
                for position in best
            )

    def _resolve_embedder(self) -> EmbeddingFunction | None:
        if self._embedder_resolved:
            return self._embedder
        self._embedder_resolved = True
        self._embedder = _paperqa_embedder(self.embedding_model)
        return self._embedder

    def _index_for(self, paper_id: str) -> _PaperIndex | None:
        elements = self.repository.get_elements(paper_id)
        sections = self.repository.get_sections(paper_id)
        fingerprint = _content_fingerprint(elements, sections)
        cached = self._cache.get(paper_id)
        if cached is not None and cached.fingerprint == fingerprint:
            self._cache.move_to_end(paper_id)
            return cached
        loaded = self._load_from_disk(paper_id)
        if loaded is not None and loaded.fingerprint == fingerprint:
            self._remember(paper_id, loaded)
            return loaded
        index = self._build_index(paper_id, elements, sections, fingerprint)
        if index is not None:
            self._remember(paper_id, index)
        return index

    def _remember(self, paper_id: str, index: _PaperIndex) -> None:
        self._cache[paper_id] = index
        self._cache.move_to_end(paper_id)
        while len(self._cache) > _INDEX_CACHE_LIMIT:
            self._cache.popitem(last=False)

    def _build_index(
        self,
        paper_id: str,
        elements: tuple[DocumentElement, ...],
        sections: tuple[Section, ...],
        fingerprint: str,
    ) -> _PaperIndex | None:
        embedder = self._resolve_embedder()
        passages = [
            passage
            for passage in build_passages(elements, sections)
            if passage.text.strip()
        ]
        if embedder is None or not passages:
            return None
        try:
            vectors = np.asarray(
                embedder([passage.text for passage in passages]), dtype=np.float32
            )
        except Exception as error:
            logger.warning(
                "building the semantic index for paper %s failed (%s); "
                "search_paper falls back to substring matching.",
                paper_id,
                error,
            )
            return None
        if vectors.ndim != 2 or vectors.shape[0] != len(passages):
            logger.warning(
                "unexpected embedding shape for paper %s; "
                "search_paper falls back to substring matching.",
                paper_id,
            )
            return None
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        index = _PaperIndex(
            passage_keys=tuple(passage.key for passage in passages),
            matrix=vectors / norms,
            fingerprint=fingerprint,
        )
        self._persist(paper_id, index)
        return index

    def _persist(self, paper_id: str, index: _PaperIndex) -> None:
        try:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            matrix_path = self.index_dir / f"{paper_id}.npz"
            sidecar_path = self.index_dir / f"{paper_id}.json"
            handle = tempfile.NamedTemporaryFile(
                dir=self.index_dir, suffix=".npz", delete=False
            )
            try:
                np.savez(
                    handle,
                    matrix=index.matrix,
                    passage_keys=np.asarray(index.passage_keys, dtype=np.str_),
                )
                handle.close()
                sidecar = tempfile.NamedTemporaryFile(
                    dir=self.index_dir, suffix=".json", delete=False
                )
                try:
                    payload = json.dumps(
                        {
                            "fingerprint": index.fingerprint,
                            "embedding_model": self.embedding_model,
                            "passage_count": len(index.passage_keys),
                        }
                    )
                    sidecar.write(payload.encode("utf-8"))
                    sidecar.close()
                    # The sidecar is written last so its presence marks a
                    # complete pair; a crash mid-write simply triggers a
                    # rebuild on the next search.
                    os.replace(sidecar.name, sidecar_path)
                finally:
                    if os.path.exists(sidecar.name):
                        os.unlink(sidecar.name)
                os.replace(handle.name, matrix_path)
            finally:
                if os.path.exists(handle.name):
                    os.unlink(handle.name)
        except OSError as error:
            logger.warning(
                "persisting the semantic index for paper %s failed (%s).",
                paper_id,
                error,
            )

    def _load_from_disk(self, paper_id: str) -> _PaperIndex | None:
        sidecar_path = self.index_dir / f"{paper_id}.json"
        matrix_path = self.index_dir / f"{paper_id}.npz"
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if sidecar.get("embedding_model") != self.embedding_model:
                return None
            with np.load(matrix_path, allow_pickle=False) as archive:
                matrix = archive["matrix"].astype(np.float32, copy=False)
                passage_keys = tuple(
                    str(value) for value in archive["passage_keys"]
                )
            fingerprint = str(sidecar.get("fingerprint", ""))
        except (OSError, ValueError, KeyError):
            return None
        if matrix.ndim != 2 or matrix.shape[0] != len(passage_keys):
            return None
        return _PaperIndex(
            passage_keys=passage_keys, matrix=matrix, fingerprint=fingerprint
        )
