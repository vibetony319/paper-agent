from dataclasses import replace
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from paper_agent.database import (
    document_elements,
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
    Note,
    Page,
    Paper,
    PaperDocument,
    ProcessingStatus,
    Section,
)


class PageReferenceError(ValueError):
    """Raised when a write references a page not owned by its paper."""


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
        sequence = self._next_processing_sequence(paper_id)
        with self.engine.begin() as connection:
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
                .where(processing_runs.c.error_summary.is_not(None))
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
