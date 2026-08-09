from dataclasses import replace
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from paper_agent.database import (
    document_elements,
    graph_edge_evidence,
    graph_edges,
    graph_node_evidence,
    graph_nodes,
    initialize_database,
    notes,
    pages,
    papers,
    processing_runs,
    sections,
)
from paper_agent.domain import (
    BoundingBox,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
    Note,
    Page,
    Paper,
    PaperDocument,
    PaperGraph,
    ProcessingStatus,
    Section,
    normalize_graph_node_name,
)


class PageReferenceError(ValueError):
    """Raised when a write references a page not owned by its paper."""


class GraphReferenceError(ValueError):
    """Raised when graph evidence or endpoints are not owned by a paper."""


class PaperRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: Engine = initialize_database(database_url)

    def create_paper(
        self,
        *,
        original_filename: str,
        stored_filename: str,
        status: ProcessingStatus = ProcessingStatus.queued,
        paper_id: str | None = None,
    ) -> Paper:
        paper = Paper(
            id=paper_id or str(uuid4()),
            original_filename=original_filename,
            stored_filename=stored_filename,
            status=status,
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(papers).values(
                    id=paper.id,
                    original_filename=paper.original_filename,
                    stored_filename=paper.stored_filename,
                    status=paper.status.value,
                    source_published=paper.source_published,
                )
            )
        return paper

    def update_paper_status(self, paper_id: str, status: ProcessingStatus) -> Paper:
        with self.engine.begin() as connection:
            connection.execute(
                update(papers).where(papers.c.id == paper_id).values(status=status.value)
            )
        paper = self.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        return paper

    def record_processing_status(
        self,
        paper_id: str,
        status: ProcessingStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            self._record_processing_status(
                connection,
                paper_id,
                status,
                stage=stage,
                error_summary=error_summary,
            )

    def get_processing_statuses(self, paper_id: str) -> tuple[ProcessingStatus, ...]:
        return self._get_processing_statuses(paper_id, stage=None)

    def get_stage_statuses(
        self, paper_id: str, stage: str
    ) -> tuple[ProcessingStatus, ...]:
        return self._get_processing_statuses(paper_id, stage=stage)

    def get_latest_stage_status(
        self, paper_id: str, stage: str
    ) -> ProcessingStatus | None:
        with self.engine.connect() as connection:
            status = connection.execute(
                select(processing_runs.c.status)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage == stage)
                .order_by(processing_runs.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
        return None if status is None else ProcessingStatus(status)

    def mark_source_published(self, paper_id: str) -> Paper:
        with self.engine.begin() as connection:
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(source_published=True)
            )
        paper = self.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        return paper

    def get_processing_error(self, paper_id: str) -> str | None:
        with self.engine.connect() as connection:
            return connection.execute(
                select(processing_runs.c.error_summary)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage.is_(None))
                .order_by(processing_runs.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()

    def get_paper(self, paper_id: str) -> Paper | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(papers).where(papers.c.id == paper_id)).mappings().one_or_none()
        return None if row is None else self._paper_from_row(row)

    def save_page(self, paper_id: str, page: Page) -> Page:
        with self.engine.begin() as connection:
            connection.execute(
                insert(pages).values(
                    id=page.id, paper_id=paper_id, number=page.number, width=page.width, height=page.height
                )
            )
        return page

    def get_pages(self, paper_id: str) -> tuple[Page, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(pages).where(pages.c.paper_id == paper_id).order_by(pages.c.number, pages.c.id)
            ).mappings()
            return tuple(Page(id=row["id"], number=row["number"], width=row["width"], height=row["height"]) for row in rows)

    def save_stage0_document(
        self,
        paper_id: str,
        stage0_pages: tuple[Page, ...],
        stage0_elements: tuple[DocumentElement, ...],
    ) -> None:
        next_order = self._next_order(document_elements, paper_id)
        persisted_elements = tuple(
            replace(element, order=next_order + index)
            for index, element in enumerate(stage0_elements)
        )
        with self.engine.begin() as connection:
            if stage0_pages:
                connection.execute(
                    insert(pages),
                    [
                        {
                            "id": page.id,
                            "paper_id": paper_id,
                            "number": page.number,
                            "width": page.width,
                            "height": page.height,
                        }
                        for page in stage0_pages
                    ],
                )
            for element in persisted_elements:
                self._require_owned_page(connection, paper_id, element.page_number)
            if persisted_elements:
                connection.execute(
                    insert(document_elements),
                    [
                        {
                            "id": element.id,
                            "paper_id": paper_id,
                            "section_id": element.section_id,
                            "kind": element.kind,
                            "text": element.text,
                            "page_number": element.page_number,
                            "bbox_x0": None if element.bbox is None else element.bbox.x0,
                            "bbox_y0": None if element.bbox is None else element.bbox.y0,
                            "bbox_x1": None if element.bbox is None else element.bbox.x1,
                            "bbox_y1": None if element.bbox is None else element.bbox.y1,
                            "location_status": element.location_status,
                            "order_index": element.order,
                        }
                        for element in persisted_elements
                    ],
                )

    def save_section(self, paper_id: str, section: Section) -> Section:
        with self.engine.begin() as connection:
            self._require_owned_page(connection, paper_id, section.page_number)
            connection.execute(
                insert(sections).values(
                    id=section.id,
                    paper_id=paper_id,
                    title=section.title,
                    page_number=section.page_number,
                    order_index=section.order,
                )
            )
        return section

    def save_stage1_document(
        self,
        paper_id: str,
        stage1_sections: tuple[Section, ...],
        stage1_elements: tuple[DocumentElement, ...],
    ) -> None:
        next_order = self._next_order(document_elements, paper_id)
        persisted_elements = tuple(
            replace(
                element,
                order=element.order if element.order is not None else next_order + index,
            )
            for index, element in enumerate(stage1_elements)
        )
        with self.engine.begin() as connection:
            for section in stage1_sections:
                self._require_owned_page(connection, paper_id, section.page_number)
            for element in persisted_elements:
                self._require_owned_page(connection, paper_id, element.page_number)
            if stage1_sections:
                connection.execute(
                    insert(sections),
                    [
                        {
                            "id": section.id,
                            "paper_id": paper_id,
                            "title": section.title,
                            "page_number": section.page_number,
                            "order_index": section.order,
                        }
                        for section in stage1_sections
                    ],
                )
            if persisted_elements:
                connection.execute(
                    insert(document_elements),
                    [
                        {
                            "id": element.id,
                            "paper_id": paper_id,
                            "section_id": element.section_id,
                            "kind": element.kind,
                            "text": element.text,
                            "page_number": element.page_number,
                            "bbox_x0": None if element.bbox is None else element.bbox.x0,
                            "bbox_y0": None if element.bbox is None else element.bbox.y0,
                            "bbox_x1": None if element.bbox is None else element.bbox.x1,
                            "bbox_y1": None if element.bbox is None else element.bbox.y1,
                            "location_status": element.location_status,
                            "order_index": element.order,
                        }
                        for element in persisted_elements
                    ],
                )

    def get_sections(self, paper_id: str) -> tuple[Section, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(sections).where(sections.c.paper_id == paper_id).order_by(sections.c.order_index, sections.c.id)
            ).mappings()
            return tuple(
                Section(id=row["id"], title=row["title"], page_number=row["page_number"], order=row["order_index"])
                for row in rows
            )

    def save_element(self, paper_id: str, element: DocumentElement) -> DocumentElement:
        order = element.order if element.order is not None else self._next_order(document_elements, paper_id)
        persisted = replace(element, order=order)
        bbox = persisted.bbox
        with self.engine.begin() as connection:
            self._require_owned_page(connection, paper_id, persisted.page_number)
            connection.execute(
                insert(document_elements).values(
                    id=persisted.id,
                    paper_id=paper_id,
                    section_id=persisted.section_id,
                    kind=persisted.kind,
                    text=persisted.text,
                    page_number=persisted.page_number,
                    bbox_x0=None if bbox is None else bbox.x0,
                    bbox_y0=None if bbox is None else bbox.y0,
                    bbox_x1=None if bbox is None else bbox.x1,
                    bbox_y1=None if bbox is None else bbox.y1,
                    location_status=persisted.location_status,
                    order_index=persisted.order,
                )
            )
        return persisted

    def get_elements(self, paper_id: str) -> tuple[DocumentElement, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(document_elements)
                .where(document_elements.c.paper_id == paper_id)
                .order_by(document_elements.c.order_index, document_elements.c.id)
            ).mappings()
            return tuple(self._element_from_row(row) for row in rows)

    def get_located_graph_source_elements(
        self, paper_id: str
    ) -> tuple[DocumentElement, ...]:
        """Return only located source records that can support graph evidence."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(document_elements)
                .where(document_elements.c.paper_id == paper_id)
                .where(document_elements.c.location_status == "located")
                .where(document_elements.c.kind.in_(("paragraph", "text_block")))
                .order_by(document_elements.c.order_index, document_elements.c.id)
            ).mappings()
            return tuple(self._element_from_row(row) for row in rows)

    def record_graph_stage_failure(
        self, paper_id: str, *, stage: str, error_summary: str
    ) -> None:
        """Durably finish a failed graph build without exposing its internal error."""
        with self.engine.begin() as connection:
            self._record_processing_status(
                connection,
                paper_id,
                ProcessingStatus.failed,
                stage=stage,
                error_summary=error_summary,
            )
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(status=ProcessingStatus.partial.value)
            )
            self._record_processing_status(
                connection,
                paper_id,
                ProcessingStatus.partial,
                error_summary=error_summary,
            )

    def replace_graph_stage(
        self,
        paper_id: str,
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> PaperGraph:
        self._require_graph_stage_replacement(stage, nodes, edges)

        with self.engine.begin() as connection:
            return self._replace_graph_stage(connection, paper_id, stage, nodes, edges)

    def replace_graph_stage_and_complete(
        self,
        paper_id: str,
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> PaperGraph:
        """Atomically replace a graph stage and durably publish its success."""
        self._require_graph_stage_replacement(stage, nodes, edges)

        with self.engine.begin() as connection:
            invalidates_deep_stage = (
                stage is GraphStage.core
                and self._stage_has_processing_state(
                    connection, paper_id, GraphStage.deep.value
                )
            )
            graph = self._replace_graph_stage(connection, paper_id, stage, nodes, edges)
            self._record_processing_status(
                connection, paper_id, ProcessingStatus.completed, stage=stage.value
            )
            if invalidates_deep_stage:
                self._record_processing_status(
                    connection,
                    paper_id,
                    ProcessingStatus.queued,
                    stage=GraphStage.deep.value,
                )
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(status=ProcessingStatus.completed.value)
            )
            self._record_processing_status(connection, paper_id, ProcessingStatus.completed)
            return graph

    def get_graph(self, paper_id: str) -> PaperGraph:
        with self.engine.connect() as connection:
            return self._get_graph(connection, paper_id)

    def get_graph_node(self, paper_id: str, node_id: str) -> GraphNode | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(graph_nodes)
                .where(graph_nodes.c.paper_id == paper_id)
                .where(graph_nodes.c.id == node_id)
            ).mappings().one_or_none()
            return None if row is None else self._graph_node_from_row(connection, paper_id, row)

    def get_graph_neighbors(self, paper_id: str, node_id: str) -> PaperGraph:
        graph = self.get_graph(paper_id)
        if not any(node.id == node_id for node in graph.nodes):
            return PaperGraph(nodes=(), edges=())
        edges = tuple(
            edge
            for edge in graph.edges
            if edge.source_node_id == node_id or edge.target_node_id == node_id
        )
        node_ids = {node_id}
        for edge in edges:
            node_ids.add(edge.source_node_id)
            node_ids.add(edge.target_node_id)
        return PaperGraph(
            nodes=tuple(node for node in graph.nodes if node.id in node_ids), edges=edges
        )

    def find_graph_paths(
        self,
        paper_id: str,
        source_node_id: str,
        target_node_id: str,
        max_depth: int,
    ) -> tuple[tuple[str, ...], ...]:
        if max_depth < 0:
            raise ValueError("max_depth must be nonnegative")
        graph = self.get_graph(paper_id)
        node_ids = {node.id for node in graph.nodes}
        if source_node_id not in node_ids or target_node_id not in node_ids:
            return ()
        paths: list[tuple[str, ...]] = []
        pending = [(source_node_id, (source_node_id,))]
        while pending:
            current_node_id, path = pending.pop(0)
            if current_node_id == target_node_id:
                paths.append(path)
                continue
            if len(path) - 1 == max_depth:
                continue
            for edge in graph.edges:
                if edge.source_node_id != current_node_id or edge.target_node_id in path:
                    continue
                pending.append((edge.target_node_id, (*path, edge.target_node_id)))
        return tuple(paths)

    def get_graph_subgraph(
        self, paper_id: str, node_ids: tuple[str, ...], depth: int
    ) -> PaperGraph:
        if depth < 0:
            raise ValueError("depth must be nonnegative")
        graph = self.get_graph(paper_id)
        included = {node.id for node in graph.nodes if node.id in node_ids}
        frontier = set(included)
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in graph.edges:
                if edge.source_node_id in frontier:
                    next_frontier.add(edge.target_node_id)
                if edge.target_node_id in frontier:
                    next_frontier.add(edge.source_node_id)
            next_frontier -= included
            included.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return PaperGraph(
            nodes=tuple(node for node in graph.nodes if node.id in included),
            edges=tuple(
                edge
                for edge in graph.edges
                if edge.source_node_id in included and edge.target_node_id in included
            ),
        )

    def create_note(self, paper_id: str, note: Note) -> Note:
        order = self._next_order(notes, paper_id)
        with self.engine.begin() as connection:
            self._require_owned_page(connection, paper_id, note.page_number)
            connection.execute(
                insert(notes).values(
                    id=note.id,
                    paper_id=paper_id,
                    element_id=note.element_id,
                    page_number=note.page_number,
                    body=note.body,
                    order_index=order,
                )
            )
        return note

    def get_notes(self, paper_id: str) -> tuple[Note, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(notes).where(notes.c.paper_id == paper_id).order_by(notes.c.order_index, notes.c.id)
            ).mappings()
            return tuple(
                Note(id=row["id"], body=row["body"], element_id=row["element_id"], page_number=row["page_number"])
                for row in rows
            )

    def get_document(self, paper_id: str) -> PaperDocument | None:
        paper = self.get_paper(paper_id)
        if paper is None:
            return None
        return PaperDocument(
            paper=paper,
            pages=self.get_pages(paper_id),
            sections=self.get_sections(paper_id),
            elements=self.get_elements(paper_id),
            notes=self.get_notes(paper_id),
        )

    def _next_order(self, table, paper_id: str) -> int:
        with self.engine.connect() as connection:
            current = connection.execute(
                select(func.max(table.c.order_index)).where(table.c.paper_id == paper_id)
            ).scalar_one()
        return 0 if current is None else current + 1

    @staticmethod
    def _require_located_graph_evidence(connection, paper_id: str, element_ids: tuple[str, ...]) -> None:
        expected = set(element_ids)
        if not expected:
            return
        found = set(
            connection.execute(
                select(document_elements.c.id)
                .where(document_elements.c.paper_id == paper_id)
                .where(document_elements.c.location_status == "located")
                .where(document_elements.c.id.in_(expected))
            ).scalars()
        )
        if found != expected:
            raise GraphReferenceError("graph evidence must be a located element owned by paper")

    @staticmethod
    def _require_owned_graph_nodes(connection, paper_id: str, node_ids: tuple[str, ...]) -> None:
        expected = set(node_ids)
        if not expected:
            return
        found = set(
            connection.execute(
                select(graph_nodes.c.id)
                .where(graph_nodes.c.paper_id == paper_id)
                .where(graph_nodes.c.id.in_(expected))
            ).scalars()
        )
        if found != expected:
            raise GraphReferenceError("graph edge endpoint does not belong to paper")

    @staticmethod
    def _record_processing_status(
        connection,
        paper_id: str,
        status: ProcessingStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        current = connection.execute(
            select(func.max(processing_runs.c.sequence)).where(
                processing_runs.c.paper_id == paper_id
            )
        ).scalar_one()
        sequence = 0 if current is None else current + 1
        connection.execute(
            insert(processing_runs).values(
                id=str(uuid4()),
                paper_id=paper_id,
                sequence=sequence,
                stage=stage,
                status=status.value,
                error_summary=error_summary,
            )
        )

    @staticmethod
    def _require_graph_stage_replacement(
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> None:
        if not isinstance(stage, GraphStage):
            raise ValueError("stage must be a GraphStage")
        if any(node.stage is not stage for node in nodes):
            raise ValueError("node stage must match replacement stage")
        if any(edge.stage is not stage for edge in edges):
            raise ValueError("edge stage must match replacement stage")

    def _replace_graph_stage(
        self,
        connection,
        paper_id: str,
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> PaperGraph:
        self._require_located_graph_evidence(
            connection,
            paper_id,
            tuple(
                element_id
                for record in (*nodes, *edges)
                for element_id in record.evidence_element_ids
            ),
        )
        self._delete_graph_stages(
            connection,
            paper_id,
            (GraphStage.deep, GraphStage.core)
            if stage is GraphStage.core
            else (GraphStage.deep,),
        )
        if nodes:
            connection.execute(
                insert(graph_nodes),
                [
                    {
                        "id": node.id,
                        "paper_id": paper_id,
                        "node_type": node.node_type,
                        "normalized_name": normalize_graph_node_name(node.name),
                        "name": node.name,
                        "summary": node.summary,
                        "stage": node.stage.value,
                    }
                    for node in nodes
                ],
            )
            connection.execute(
                insert(graph_node_evidence),
                [
                    {
                        "paper_id": paper_id,
                        "node_id": node.id,
                        "element_id": element_id,
                    }
                    for node in nodes
                    for element_id in node.evidence_element_ids
                ],
            )
        self._require_owned_graph_nodes(
            connection,
            paper_id,
            tuple(
                node_id
                for edge in edges
                for node_id in (edge.source_node_id, edge.target_node_id)
            ),
        )
        if edges:
            connection.execute(
                insert(graph_edges),
                [
                    {
                        "id": edge.id,
                        "paper_id": paper_id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "relation_type": edge.relation_type,
                        "stage": edge.stage.value,
                    }
                    for edge in edges
                ],
            )
            connection.execute(
                insert(graph_edge_evidence),
                [
                    {
                        "paper_id": paper_id,
                        "edge_id": edge.id,
                        "element_id": element_id,
                    }
                    for edge in edges
                    for element_id in edge.evidence_element_ids
                ],
            )
        return self._get_graph(connection, paper_id)

    @staticmethod
    def _stage_has_processing_state(
        connection, paper_id: str, stage: str
    ) -> bool:
        return (
            connection.execute(
                select(processing_runs.c.id)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage == stage)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    @staticmethod
    def _delete_graph_stages(connection, paper_id: str, stages: tuple[GraphStage, ...]) -> None:
        for stage in stages:
            edge_ids = select(graph_edges.c.id).where(
                graph_edges.c.paper_id == paper_id,
                graph_edges.c.stage == stage.value,
            )
            connection.execute(
                delete(graph_edge_evidence).where(
                    graph_edge_evidence.c.paper_id == paper_id,
                    graph_edge_evidence.c.edge_id.in_(edge_ids),
                )
            )
            connection.execute(
                delete(graph_edges).where(
                    graph_edges.c.paper_id == paper_id,
                    graph_edges.c.stage == stage.value,
                )
            )
            node_ids = select(graph_nodes.c.id).where(
                graph_nodes.c.paper_id == paper_id,
                graph_nodes.c.stage == stage.value,
            )
            connection.execute(
                delete(graph_node_evidence).where(
                    graph_node_evidence.c.paper_id == paper_id,
                    graph_node_evidence.c.node_id.in_(node_ids),
                )
            )
            connection.execute(
                delete(graph_nodes).where(
                    graph_nodes.c.paper_id == paper_id,
                    graph_nodes.c.stage == stage.value,
                )
            )

    @classmethod
    def _get_graph(cls, connection, paper_id: str) -> PaperGraph:
        node_rows = connection.execute(
            select(graph_nodes)
            .where(graph_nodes.c.paper_id == paper_id)
            .order_by(graph_nodes.c.id)
        ).mappings()
        nodes = tuple(cls._graph_node_from_row(connection, paper_id, row) for row in node_rows)
        edge_rows = connection.execute(
            select(graph_edges)
            .where(graph_edges.c.paper_id == paper_id)
            .order_by(graph_edges.c.id)
        ).mappings()
        edges = tuple(cls._graph_edge_from_row(connection, paper_id, row) for row in edge_rows)
        return PaperGraph(nodes=nodes, edges=edges)

    @staticmethod
    def _graph_node_from_row(connection, paper_id: str, row) -> GraphNode:
        evidence_ids = tuple(
            connection.execute(
                select(graph_node_evidence.c.element_id)
                .where(graph_node_evidence.c.paper_id == paper_id)
                .where(graph_node_evidence.c.node_id == row["id"])
                .order_by(graph_node_evidence.c.element_id)
            ).scalars()
        )
        return GraphNode(
            id=row["id"],
            node_type=row["node_type"],
            name=row["name"],
            summary=row["summary"],
            stage=GraphStage(row["stage"]),
            evidence_element_ids=evidence_ids,
        )

    @staticmethod
    def _graph_edge_from_row(connection, paper_id: str, row) -> GraphEdge:
        evidence_ids = tuple(
            connection.execute(
                select(graph_edge_evidence.c.element_id)
                .where(graph_edge_evidence.c.paper_id == paper_id)
                .where(graph_edge_evidence.c.edge_id == row["id"])
                .order_by(graph_edge_evidence.c.element_id)
            ).scalars()
        )
        return GraphEdge(
            id=row["id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            relation_type=row["relation_type"],
            stage=GraphStage(row["stage"]),
            evidence_element_ids=evidence_ids,
        )

    def _next_processing_sequence(self, paper_id: str) -> int:
        with self.engine.connect() as connection:
            current = connection.execute(
                select(func.max(processing_runs.c.sequence)).where(
                    processing_runs.c.paper_id == paper_id
                )
            ).scalar_one()
        return 0 if current is None else current + 1

    def _get_processing_statuses(
        self, paper_id: str, *, stage: str | None
    ) -> tuple[ProcessingStatus, ...]:
        statement = select(processing_runs.c.status).where(
            processing_runs.c.paper_id == paper_id
        )
        if stage is None:
            statement = statement.where(processing_runs.c.stage.is_(None))
        else:
            statement = statement.where(processing_runs.c.stage == stage)
        with self.engine.connect() as connection:
            rows = connection.execute(statement.order_by(processing_runs.c.sequence))
        return tuple(ProcessingStatus(row.status) for row in rows)

    @staticmethod
    def _require_owned_page(connection, paper_id: str, page_number: int | None) -> None:
        if page_number is None:
            return
        page_id = connection.execute(
            select(pages.c.id)
            .where(pages.c.paper_id == paper_id)
            .where(pages.c.number == page_number)
        ).scalar_one_or_none()
        if page_id is None:
            raise PageReferenceError("page target does not belong to paper")

    @staticmethod
    def _paper_from_row(row) -> Paper:
        return Paper(
            id=row["id"],
            original_filename=row["original_filename"],
            stored_filename=row["stored_filename"],
            status=ProcessingStatus(row["status"]),
            source_published=bool(row["source_published"]),
        )

    @staticmethod
    def _element_from_row(row) -> DocumentElement:
        bbox = None
        if row["bbox_x0"] is not None:
            bbox = BoundingBox(
                x0=row["bbox_x0"],
                y0=row["bbox_y0"],
                x1=row["bbox_x1"],
                y1=row["bbox_y1"],
            )
        return DocumentElement(
            id=row["id"],
            kind=row["kind"],
            text=row["text"],
            page_number=row["page_number"],
            bbox=bbox,
            section_id=row["section_id"],
            location_status=row["location_status"],
            order=row["order_index"],
        )
