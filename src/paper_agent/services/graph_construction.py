from __future__ import annotations

from dataclasses import dataclass
import json
from unicodedata import normalize

from sqlalchemy.exc import SQLAlchemyError

from paper_agent.domain import (
    CORE_NODE_TYPES,
    DEEP_NODE_TYPES,
    RELATION_TYPES,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
    PaperGraph,
    ProcessingStatus,
    Section,
)
from paper_agent.models.vllm import VllmResponseError, VllmStructuredClient
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.services.graph_extraction import (
    EdgeCandidate,
    GraphExtractionError,
    NodeCandidate,
    deduplicate_nodes,
    edge_output_schema,
    node_output_schema,
    parse_edge_candidates,
    parse_node_candidates,
)
from paper_agent.storage import GraphReferenceError, PaperRepository


class GraphBuildUnavailableError(RuntimeError):
    """Raised when optional reasoning configuration is not available."""


class GraphBuildPrerequisiteError(RuntimeError):
    """Raised when the paper has not completed a required earlier stage."""


class GraphBuildConflictError(RuntimeError):
    """Raised when a durable request is already running under the same key."""


_NODE_SYSTEM_PROMPT = (
    "Return only the supplied JSON schema. Cite only the listed source element IDs. "
    "Use no external knowledge. Omit unsupported candidates."
)
_EDGE_SYSTEM_PROMPT = (
    "Return only the supplied JSON schema. Cite only the listed source element IDs. "
    "Use no external knowledge. Omit unsupported candidates."
)
_FAILURE_SUMMARIES = {
    GraphStage.core: "The core knowledge graph could not be constructed.",
    GraphStage.deep: "The deep knowledge graph could not be constructed.",
}


@dataclass(frozen=True)
class _SourceScope:
    section_title: str
    elements: tuple[DocumentElement, ...]


class GraphConstructionService:
    def __init__(self, *, repository: PaperRepository) -> None:
        self.repository = repository

    def build_core(
        self,
        paper_id: str,
        *,
        client: VllmStructuredClient | None,
        model_snapshot: ModelSnapshot,
        request_id: str,
    ) -> PaperGraph:
        return self._build(
            paper_id,
            GraphStage.core,
            client=client,
            model_snapshot=model_snapshot,
            request_id=request_id,
        )

    def build_deep(
        self,
        paper_id: str,
        *,
        client: VllmStructuredClient | None,
        model_snapshot: ModelSnapshot,
        request_id: str,
    ) -> PaperGraph:
        return self._build(
            paper_id,
            GraphStage.deep,
            client=client,
            model_snapshot=model_snapshot,
            request_id=request_id,
        )

    def require_prerequisites(self, paper_id: str, stage: GraphStage) -> None:
        self._require_prerequisites(paper_id, stage)

    def _build(
        self,
        paper_id: str,
        stage: GraphStage,
        *,
        client: VllmStructuredClient | None,
        model_snapshot: ModelSnapshot,
        request_id: str,
    ) -> PaperGraph:
        self._require_prerequisites(paper_id, stage)
        if client is None:
            raise GraphBuildUnavailableError("Reasoning model configuration is unavailable.")
        stage_name = stage.value
        durable_status = self.repository.get_graph_build_status(
            paper_id, stage_name, request_id
        )
        if durable_status is ProcessingStatus.completed:
            return self.repository.get_graph(paper_id)
        if durable_status is ProcessingStatus.running:
            raise GraphBuildConflictError(
                "Graph build is already running for this request."
            )
        try:
            self.repository.record_processing_status(
                paper_id,
                ProcessingStatus.queued,
                stage=stage_name,
                model_profile_id=model_snapshot.profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            self.repository.record_processing_status(
                paper_id,
                ProcessingStatus.running,
                stage=stage_name,
                model_profile_id=model_snapshot.profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            scopes = self._source_scopes(paper_id)
            nodes = self._extract_nodes(stage, scopes, client)
            edges = self._extract_edges(stage, scopes, nodes, client)
            graph = self.repository.replace_graph_stage_and_complete(
                paper_id,
                stage,
                nodes,
                edges,
                model_profile_id=model_snapshot.profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            return graph
        except (
            VllmResponseError,
            GraphExtractionError,
            GraphReferenceError,
            SQLAlchemyError,
        ):
            self._record_failure(
                paper_id,
                stage,
                model_profile_id=model_snapshot.profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            raise
        except Exception:
            self._record_failure(
                paper_id,
                stage,
                model_profile_id=model_snapshot.profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            raise

    def _require_prerequisites(self, paper_id: str, stage: GraphStage) -> None:
        if self.repository.get_paper(paper_id) is None:
            raise GraphBuildPrerequisiteError("Paper graph prerequisites are not complete.")
        if self.repository.get_latest_stage_status(paper_id, "stage1") != ProcessingStatus.completed:
            raise GraphBuildPrerequisiteError("Paper graph prerequisites are not complete.")
        if (
            stage is GraphStage.deep
            and self.repository.get_latest_stage_status(paper_id, "stage2")
            != ProcessingStatus.completed
        ):
            raise GraphBuildPrerequisiteError("Paper graph prerequisites are not complete.")

    def _source_scopes(self, paper_id: str) -> tuple[_SourceScope, ...]:
        sections = self.repository.get_sections(paper_id)
        all_elements = self.repository.get_elements(paper_id)
        located_sources = self.repository.get_located_graph_source_elements(paper_id)
        located_paragraphs = tuple(
            element for element in located_sources if element.kind == "paragraph"
        )
        text_blocks = tuple(
            element for element in located_sources if element.kind == "text_block"
        )

        scopes = [
            _SourceScope(section.title, self._section_sources(section, all_elements, located_paragraphs, text_blocks))
            for section in sections
        ]
        unsectioned = tuple(
            element for element in located_paragraphs if element.section_id is None
        )
        if unsectioned:
            scopes.append(_SourceScope("Unsectioned", unsectioned))
        scopes = [scope for scope in scopes if scope.elements]
        if not scopes and text_blocks:
            # Stage1 conversion can yield no sections (e.g. two-column PDFs with
            # no detected headings); located page text blocks remain the only
            # usable evidence, so group them per page as fallback scopes.
            scopes = self._page_scopes(text_blocks)
        if not scopes:
            raise GraphBuildPrerequisiteError(
                "No located source elements are available for graph construction."
            )
        return tuple(scopes)

    @staticmethod
    def _page_scopes(
        text_blocks: tuple[DocumentElement, ...]
    ) -> list[_SourceScope]:
        blocks_by_page: dict[int | None, list[DocumentElement]] = {}
        for block in text_blocks:
            blocks_by_page.setdefault(block.page_number, []).append(block)
        return [
            _SourceScope(
                "Unsectioned" if page is None else f"Page {page}",
                tuple(blocks),
            )
            for page, blocks in sorted(
                blocks_by_page.items(), key=lambda item: (item[0] is None, item[0])
            )
        ]

    @staticmethod
    def _section_sources(
        section: Section,
        all_elements: tuple[DocumentElement, ...],
        located_paragraphs: tuple[DocumentElement, ...],
        text_blocks: tuple[DocumentElement, ...],
    ) -> tuple[DocumentElement, ...]:
        paragraphs = tuple(
            element
            for element in located_paragraphs
            if element.section_id == section.id
        )
        if paragraphs:
            return paragraphs
        section_text = {
            _normalized_text(element.text)
            for element in all_elements
            if element.kind == "paragraph"
            and element.location_status == "unlocated"
            and element.section_id == section.id
            and _normalized_text(element.text)
        }
        if section_text:
            return tuple(
                block
                for block in text_blocks
                if _normalized_text(block.text) in section_text
            )
        if section.page_number is not None:
            return tuple(
                block for block in text_blocks if block.page_number == section.page_number
            )
        return ()

    def _extract_nodes(
        self,
        stage: GraphStage,
        scopes: tuple[_SourceScope, ...],
        client: VllmStructuredClient,
    ) -> tuple[GraphNode, ...]:
        candidates: list[NodeCandidate] = []
        for scope in scopes:
            evidence_ids = frozenset(element.id for element in scope.elements)
            payload = client.generate_json(
                system_prompt=_stage_system_prompt(_NODE_SYSTEM_PROMPT, stage),
                user_prompt=_node_prompt(scope),
                schema_name="paper_graph_nodes",
                schema=node_output_schema(),
            )
            candidates.extend(
                parse_node_candidates(
                    payload, stage=stage, allowed_evidence_ids=evidence_ids
                )
            )
        return deduplicate_nodes(tuple(candidates), stage)

    def _extract_edges(
        self,
        stage: GraphStage,
        scopes: tuple[_SourceScope, ...],
        nodes: tuple[GraphNode, ...],
        client: VllmStructuredClient,
    ) -> tuple[GraphEdge, ...]:
        candidates: list[EdgeCandidate] = []
        for scope in scopes:
            evidence_ids = frozenset(element.id for element in scope.elements)
            scope_nodes = tuple(
                node
                for node in nodes
                if set(node.evidence_element_ids).intersection(evidence_ids)
            )
            payload = client.generate_json(
                system_prompt=_stage_system_prompt(_EDGE_SYSTEM_PROMPT, stage),
                user_prompt=_edge_prompt(scope, scope_nodes),
                schema_name="paper_graph_edges",
                schema=edge_output_schema(),
            )
            candidates.extend(
                parse_edge_candidates(
                    payload,
                    stage=stage,
                    allowed_node_ids=frozenset(node.id for node in scope_nodes),
                    allowed_evidence_ids=evidence_ids,
                )
            )

        evidence_by_id = {
            element.id: element for scope in scopes for element in scope.elements
        }
        cross_evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for node in nodes
                for evidence_id in node.evidence_element_ids
            )
        )
        cross_elements = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in cross_evidence_ids
            if evidence_id in evidence_by_id
        )
        cross_payload = client.generate_json(
            system_prompt=_stage_system_prompt(_EDGE_SYSTEM_PROMPT, stage),
            user_prompt=_cross_section_edge_prompt(nodes, cross_elements),
            schema_name="paper_graph_cross_section_edges",
            schema=edge_output_schema(),
        )
        candidates.extend(
            parse_edge_candidates(
                cross_payload,
                stage=stage,
                allowed_node_ids=frozenset(node.id for node in nodes),
                allowed_evidence_ids=frozenset(element.id for element in cross_elements),
            )
        )
        return _deduplicate_edges(tuple(candidates), stage)

    def _record_failure(
        self,
        paper_id: str,
        stage: GraphStage,
        *,
        model_profile_id: str,
        model_snapshot: ModelSnapshot,
        request_id: str,
    ) -> None:
        try:
            self.repository.record_graph_stage_failure(
                paper_id,
                stage=stage.value,
                error_summary=_FAILURE_SUMMARIES[stage],
                model_profile_id=model_profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
        except Exception:
            # A failed best-effort status write must not replace the original error.
            pass


def _stage_system_prompt(policy: str, stage: GraphStage) -> str:
    node_types = CORE_NODE_TYPES if stage is GraphStage.core else DEEP_NODE_TYPES
    return (
        f"{policy} Allowed node types for {stage.value}: {', '.join(sorted(node_types))}. "
        f"Allowed relation types: {', '.join(sorted(RELATION_TYPES))}."
    )


def _node_prompt(scope: _SourceScope) -> str:
    return _json_prompt(
        {
            "section": {"title": scope.section_title},
            "source_elements": _source_elements(scope.elements),
        }
    )


def _edge_prompt(scope: _SourceScope, nodes: tuple[GraphNode, ...]) -> str:
    return _json_prompt(
        {
            "section": {"title": scope.section_title},
            "nodes": _prompt_nodes(nodes),
            "source_elements": _source_elements(scope.elements),
        }
    )


def _cross_section_edge_prompt(
    nodes: tuple[GraphNode, ...], evidence: tuple[DocumentElement, ...]
) -> str:
    return _json_prompt(
        {
            "nodes": _prompt_nodes(nodes),
            "source_elements": [
                {"id": element.id, "text": element.text} for element in evidence
            ],
        }
    )


def _source_elements(elements: tuple[DocumentElement, ...]) -> list[dict[str, str]]:
    return [
        {"id": element.id, "kind": element.kind, "text": element.text}
        for element in elements
    ]


def _prompt_nodes(nodes: tuple[GraphNode, ...]) -> list[dict[str, str]]:
    return [
        {"id": node.id, "name": node.name, "summary": node.summary}
        for node in nodes
    ]


def _json_prompt(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _deduplicate_edges(
    candidates: tuple[EdgeCandidate, ...], stage: GraphStage
) -> tuple[GraphEdge, ...]:
    merged: dict[tuple[str, str, str], EdgeCandidate] = {}
    evidence_by_key: dict[tuple[str, str, str], list[str]] = {}
    for candidate in candidates:
        key = (
            candidate.source_node_id,
            candidate.target_node_id,
            candidate.relation_type,
        )
        merged.setdefault(key, candidate)
        evidence_by_key.setdefault(key, []).extend(candidate.evidence_element_ids)
    return tuple(
        GraphEdge(
            source_node_id=candidate.source_node_id,
            target_node_id=candidate.target_node_id,
            relation_type=candidate.relation_type,
            stage=stage,
            evidence_element_ids=tuple(dict.fromkeys(evidence_by_key[key])),
        )
        for key, candidate in merged.items()
    )


def _normalized_text(text: str) -> str:
    return " ".join(normalize("NFKC", text).split()).casefold()
