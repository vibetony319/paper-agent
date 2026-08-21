"""Strict, deterministic paper and graph tools for the paper agent."""

from dataclasses import dataclass
from typing import ClassVar
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from paper_agent.domain import DocumentElement, GraphEdge, GraphNode, PaperGraph, Section
from paper_agent.storage import PaperRepository


class AgentToolError(ValueError):
    """Raised when a paper-agent tool request is invalid or unavailable."""


@dataclass(frozen=True)
class ToolExecution:
    name: str
    content: dict[str, object]
    evidence_element_ids: tuple[str, ...]


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def _reject_blank_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("must not be blank")
        return value


class _SearchPaperArguments(_ToolArguments):
    query: str
    limit: int = Field(default=5, ge=1, le=10)


class _ReadElementArguments(_ToolArguments):
    element_id: str


class _ReadSectionArguments(_ToolArguments):
    section_id: str


class _SearchGraphArguments(_ToolArguments):
    query: str
    limit: int = Field(default=5, ge=1, le=10)


class _InspectNodeArguments(_ToolArguments):
    node_id: str


class _ExpandGraphArguments(_ToolArguments):
    node_id: str
    depth: int = Field(default=1, ge=0, le=3)


class _FindGraphPathsArguments(_ToolArguments):
    source_node_id: str
    target_node_id: str
    max_depth: int = Field(default=3, ge=0, le=3)


def _public_element(element: DocumentElement) -> dict[str, object]:
    return {
        "id": element.id,
        "kind": element.kind,
        "text": element.text,
        "page_number": element.page_number,
        "bbox": None
        if element.bbox is None
        else {
            "x0": element.bbox.x0,
            "y0": element.bbox.y0,
            "x1": element.bbox.x1,
            "y1": element.bbox.y1,
        },
        "section_id": element.section_id,
        "location_status": element.location_status,
        "order": element.order,
    }


def _public_section(section: Section) -> dict[str, object]:
    return {
        "id": section.id,
        "title": section.title,
        "page_number": section.page_number,
        "order": section.order,
    }


def _public_node(node: GraphNode) -> dict[str, object]:
    return {
        "id": node.id,
        "node_type": node.node_type,
        "name": node.name,
        "summary": node.summary,
        "stage": node.stage.value,
        "evidence_element_ids": list(node.evidence_element_ids),
    }


def _public_edge(edge: GraphEdge) -> dict[str, object]:
    return {
        "id": edge.id,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "relation_type": edge.relation_type,
        "stage": edge.stage.value,
        "evidence_element_ids": list(edge.evidence_element_ids),
    }


def _public_graph(graph: PaperGraph) -> dict[str, object]:
    return {
        "nodes": [_public_node(node) for node in graph.nodes],
        "edges": [_public_edge(edge) for edge in graph.edges],
    }


class PaperToolRegistry:
    _tool_models: ClassVar[dict[str, type[_ToolArguments]]] = {
        "search_paper": _SearchPaperArguments,
        "read_element": _ReadElementArguments,
        "read_section": _ReadSectionArguments,
        "search_graph": _SearchGraphArguments,
        "inspect_node": _InspectNodeArguments,
        "expand_graph": _ExpandGraphArguments,
        "find_graph_paths": _FindGraphPathsArguments,
    }
    _descriptions: ClassVar[dict[str, str]] = {
        "search_paper": "Find document elements by normalized text substring.",
        "read_element": "Read one document element from the active paper.",
        "read_section": "Read one section and its document elements from the active paper.",
        "search_graph": "Find graph nodes by normalized name or summary substring.",
        "inspect_node": "Inspect one graph node from the active paper.",
        "expand_graph": "Expand a graph neighborhood from one node.",
        "find_graph_paths": "Find directed graph paths between two nodes.",
    }

    def __init__(self, repository: PaperRepository) -> None:
        self.repository = repository
        self._handlers = {
            "search_paper": self._search_paper,
            "read_element": self._read_element,
            "read_section": self._read_section,
            "search_graph": self._search_graph,
            "inspect_node": self._inspect_node,
            "expand_graph": self._expand_graph,
            "find_graph_paths": self._find_graph_paths,
        }

    def definitions(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._descriptions[name],
                    "strict": True,
                    "parameters": model.model_json_schema(),
                },
            }
            for name, model in self._tool_models.items()
        )

    def execute(
        self, *, paper_id: str, name: str, arguments: dict[str, object]
    ) -> ToolExecution:
        handler = self._handlers.get(name)
        model = self._tool_models.get(name)
        if handler is None or model is None:
            raise AgentToolError("requested paper tool is unavailable")
        if not isinstance(arguments, dict):
            raise AgentToolError("invalid tool arguments")
        if self.repository.get_document(paper_id) is None:
            raise AgentToolError("active paper is unavailable")
        try:
            parsed = model.model_validate(arguments)
        except ValidationError as error:
            raise AgentToolError("invalid tool arguments") from None
        return handler(paper_id, parsed)

    def _search_paper(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        query = _normalized_search_text(arguments.query)
        elements = tuple(
            element
            for element in self.repository.get_elements(paper_id)
            if query in _normalized_search_text(element.text)
        )[: arguments.limit]
        return ToolExecution(
            name="search_paper",
            content={"elements": [_public_element(element) for element in elements]},
            evidence_element_ids=self._located_evidence_ids(paper_id, elements),
        )

    def _read_element(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        element = next(
            (
                candidate
                for candidate in self.repository.get_elements(paper_id)
                if candidate.id == arguments.element_id
            ),
            None,
        )
        if element is None:
            raise AgentToolError("requested element is unavailable")
        return ToolExecution(
            name="read_element",
            content={"element": _public_element(element)},
            evidence_element_ids=self._located_evidence_ids(paper_id, (element,)),
        )

    def _read_section(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        section = next(
            (candidate for candidate in self.repository.get_sections(paper_id) if candidate.id == arguments.section_id),
            None,
        )
        if section is None:
            raise AgentToolError("requested section is unavailable")
        elements = tuple(
            element
            for element in self.repository.get_elements(paper_id)
            if element.section_id == section.id
        )
        return ToolExecution(
            name="read_section",
            content={
                "section": _public_section(section),
                "elements": [_public_element(element) for element in elements],
            },
            evidence_element_ids=self._located_evidence_ids(paper_id, elements),
        )

    def _search_graph(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        query = _normalized_search_text(arguments.query)
        nodes = tuple(
            node
            for node in self.repository.get_graph(paper_id).nodes
            if query in _normalized_search_text(node.name)
            or query in _normalized_search_text(node.summary)
        )[: arguments.limit]
        return ToolExecution(
            name="search_graph",
            content={"nodes": [_public_node(node) for node in nodes]},
            evidence_element_ids=self._graph_evidence_ids(paper_id, nodes, ()),
        )

    def _inspect_node(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        node = self.repository.get_graph_node(paper_id, arguments.node_id)
        if node is None:
            raise AgentToolError("requested graph node is unavailable")
        return ToolExecution(
            name="inspect_node",
            content={"node": _public_node(node)},
            evidence_element_ids=self._graph_evidence_ids(paper_id, (node,), ()),
        )

    def _expand_graph(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        if self.repository.get_graph_node(paper_id, arguments.node_id) is None:
            raise AgentToolError("requested graph node is unavailable")
        graph = self.repository.get_graph_subgraph(
            paper_id, (arguments.node_id,), arguments.depth
        )
        return ToolExecution(
            name="expand_graph",
            content=_public_graph(graph),
            evidence_element_ids=self._graph_evidence_ids(
                paper_id, graph.nodes, graph.edges
            ),
        )

    def _find_graph_paths(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        graph = self.repository.get_graph(paper_id)
        node_ids = {node.id for node in graph.nodes}
        if (
            arguments.source_node_id not in node_ids
            or arguments.target_node_id not in node_ids
        ):
            raise AgentToolError("requested graph node is unavailable")
        paths = self.repository.find_graph_paths(
            paper_id,
            arguments.source_node_id,
            arguments.target_node_id,
            arguments.max_depth,
        )
        path_node_ids = {node_id for path in paths for node_id in path}
        path_edges = tuple(
            edge
            for edge in graph.edges
            if any(
                (edge.source_node_id, edge.target_node_id)
                in zip(path, path[1:])
                for path in paths
            )
        )
        return ToolExecution(
            name="find_graph_paths",
            content={"paths": [list(path) for path in paths]},
            evidence_element_ids=self._graph_evidence_ids(
                paper_id,
                tuple(node for node in graph.nodes if node.id in path_node_ids),
                path_edges,
            ),
        )

    def _located_evidence_ids(
        self, paper_id: str, elements: tuple[DocumentElement, ...]
    ) -> tuple[str, ...]:
        return tuple(
            element.id for element in elements if element.location_status == "located"
        )

    def _graph_evidence_ids(
        self,
        paper_id: str,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> tuple[str, ...]:
        located_ids = {
            element.id
            for element in self.repository.get_elements(paper_id)
            if element.location_status == "located"
        }
        ordered_ids = (
            element_id
            for record in (*nodes, *edges)
            for element_id in record.evidence_element_ids
        )
        return tuple(dict.fromkeys(element_id for element_id in ordered_ids if element_id in located_ids))


def _normalized_search_text(value: str) -> str:
    return normalize("NFKC", value).casefold()
