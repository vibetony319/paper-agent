from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from paper_agent.domain import (
    CORE_NODE_TYPES,
    DEEP_NODE_TYPES,
    RELATION_TYPES,
    GraphNode,
    GraphStage,
)


class GraphExtractionError(ValueError):
    """Raised when a graph candidate response cannot be trusted."""


@dataclass(frozen=True)
class NodeCandidate:
    local_id: str
    node_type: str
    name: str
    summary: str
    evidence_element_ids: tuple[str, ...]


@dataclass(frozen=True)
class EdgeCandidate:
    source_node_id: str
    target_node_id: str
    relation_type: str
    evidence_element_ids: tuple[str, ...]


_NonblankText = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


class _CandidateNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    local_id: _NonblankText
    node_type: _NonblankText
    name: _NonblankText
    summary: _NonblankText
    evidence_element_ids: list[_NonblankText]

    @field_validator("local_id", "node_type", "name", "summary")
    @classmethod
    def _require_trimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("text fields must be trimmed")
        return value

    @field_validator("evidence_element_ids")
    @classmethod
    def _require_evidence_ids(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("evidence element IDs are required")
        if any(value != value.strip() for value in values):
            raise ValueError("evidence element IDs must be trimmed")
        if len(values) != len(set(values)):
            raise ValueError("evidence element IDs must be unique")
        return values


class _NodeCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nodes: list[_CandidateNode]


class _CandidateEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_node_id: _NonblankText
    target_node_id: _NonblankText
    relation_type: _NonblankText
    evidence_element_ids: list[_NonblankText]

    @field_validator("source_node_id", "target_node_id", "relation_type")
    @classmethod
    def _require_trimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("text fields must be trimmed")
        return value

    @field_validator("evidence_element_ids")
    @classmethod
    def _require_evidence_ids(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("evidence element IDs are required")
        if any(value != value.strip() for value in values):
            raise ValueError("evidence element IDs must be trimmed")
        if len(values) != len(set(values)):
            raise ValueError("evidence element IDs must be unique")
        return values


class _EdgeCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    edges: list[_CandidateEdge]


def parse_node_candidates(
    payload: dict, *, stage: GraphStage, allowed_evidence_ids: frozenset[str]
) -> tuple[NodeCandidate, ...]:
    """Parse only evidence-backed node candidates for the requested graph stage."""
    _require_stage(stage)
    response = _validate_payload(_NodeCandidateResponse, payload, "node")
    allowed_node_types = (
        CORE_NODE_TYPES if stage is GraphStage.core else DEEP_NODE_TYPES
    )
    allowed_evidence = set(allowed_evidence_ids)

    candidates: list[NodeCandidate] = []
    for node in response.nodes:
        if node.node_type not in allowed_node_types:
            raise GraphExtractionError("node type is not allowed for graph stage")
        _require_supplied_evidence(node.evidence_element_ids, allowed_evidence)
        candidates.append(
            NodeCandidate(
                local_id=node.local_id,
                node_type=node.node_type,
                name=node.name,
                summary=node.summary,
                evidence_element_ids=tuple(node.evidence_element_ids),
            )
        )
    return tuple(candidates)


def parse_edge_candidates(
    payload: dict,
    *,
    stage: GraphStage,
    allowed_node_ids: frozenset[str],
    allowed_evidence_ids: frozenset[str],
) -> tuple[EdgeCandidate, ...]:
    """Parse only edges whose endpoints and evidence were supplied to the model."""
    _require_stage(stage)
    response = _validate_payload(_EdgeCandidateResponse, payload, "edge")
    allowed_nodes = set(allowed_node_ids)
    allowed_evidence = set(allowed_evidence_ids)

    candidates: list[EdgeCandidate] = []
    for edge in response.edges:
        if (
            edge.source_node_id not in allowed_nodes
            or edge.target_node_id not in allowed_nodes
        ):
            raise GraphExtractionError("edge node IDs must come from supplied nodes")
        if edge.source_node_id == edge.target_node_id:
            raise GraphExtractionError("edge endpoints must be different")
        if edge.relation_type not in RELATION_TYPES:
            raise GraphExtractionError("edge relation type is not allowed")
        _require_supplied_evidence(edge.evidence_element_ids, allowed_evidence)
        candidates.append(
            EdgeCandidate(
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation_type=edge.relation_type,
                evidence_element_ids=tuple(edge.evidence_element_ids),
            )
        )
    return tuple(candidates)


def deduplicate_nodes(
    candidates: tuple[NodeCandidate, ...], stage: GraphStage
) -> tuple[GraphNode, ...]:
    """Merge same-type, normalized-name candidates in first-seen order."""
    _require_stage(stage)
    groups: dict[tuple[str, str], list[NodeCandidate]] = {}
    for candidate in candidates:
        key = (candidate.node_type, _normalized_name(candidate.name))
        groups.setdefault(key, []).append(candidate)

    nodes: list[GraphNode] = []
    for group in groups.values():
        first = group[0]
        summary = next(
            (candidate.summary for candidate in group if candidate.summary.strip()),
            "",
        )
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for candidate in group
                for evidence_id in candidate.evidence_element_ids
            )
        )
        nodes.append(
            GraphNode(
                node_type=first.node_type,
                name=first.name,
                summary=summary,
                stage=stage,
                evidence_element_ids=evidence_ids,
            )
        )
    return tuple(nodes)


def node_output_schema() -> dict[str, object]:
    """Return the strict JSON shape accepted by ``parse_node_candidates``."""
    return _inline_schema_references(_NodeCandidateResponse.model_json_schema())


def edge_output_schema() -> dict[str, object]:
    """Return the strict JSON shape accepted by ``parse_edge_candidates``."""
    return _inline_schema_references(_EdgeCandidateResponse.model_json_schema())


def _validate_payload(
    model: type[_NodeCandidateResponse] | type[_EdgeCandidateResponse],
    payload: dict,
    kind: str,
) -> _NodeCandidateResponse | _EdgeCandidateResponse:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise GraphExtractionError(f"invalid {kind} candidate response: {error}") from error


def _require_stage(stage: GraphStage) -> None:
    if not isinstance(stage, GraphStage):
        raise GraphExtractionError("stage must be a GraphStage")


def _require_supplied_evidence(
    evidence_ids: list[str], allowed_evidence_ids: set[str]
) -> None:
    if not evidence_ids or not set(evidence_ids).issubset(allowed_evidence_ids):
        raise GraphExtractionError(
            "candidate evidence must come from the supplied source elements"
        )


def _normalized_name(name: str) -> str:
    return " ".join(normalize("NFKC", name).split()).casefold()


def _inline_schema_references(schema: dict[str, object]) -> dict[str, object]:
    schema = deepcopy(schema)
    definitions = schema.pop("$defs", {})

    def inline(value: object) -> object:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                reference = value["$ref"]
                if isinstance(reference, str) and reference.startswith("#/$defs/"):
                    return inline(deepcopy(definitions[reference.removeprefix("#/$defs/")]))
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return inline(schema)
