from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from paper_agent.domain import (
    AgentMessageRole,
    Conversation,
    CitationSnapshot,
    ConversationMessage,
    DocumentElement,
    Note,
    Page,
    Paper,
    PaperDocument,
    ProcessingStatus,
    Section,
)
from paper_agent.model_profiles import ModelSnapshot

if TYPE_CHECKING:
    from paper_agent.model_profiles import ModelCapabilities
    from paper_agent.services.context_budget import ContextUsage
    from paper_agent.services.model_profiles import ModelProfileView


@dataclass(frozen=True)
class UploadPayload:
    filename: str
    content: bytes
    media_type: str | None


class ModelSnapshotResponse(BaseModel):
    profile_id: UUID
    display_name: str
    base_url: str
    model_name: str
    revision: int

    @classmethod
    def from_snapshot(cls, snapshot: ModelSnapshot) -> "ModelSnapshotResponse":
        return cls(
            profile_id=UUID(snapshot.profile_id),
            display_name=snapshot.display_name,
            base_url=snapshot.base_url,
            model_name=snapshot.model_name,
            revision=snapshot.revision,
        )


@dataclass(frozen=True)
class PaperSummary:
    id: str
    original_filename: str
    status: ProcessingStatus
    stage0_status: ProcessingStatus
    stage1_status: ProcessingStatus
    error: str | None = None

    @classmethod
    def from_paper(
        cls,
        paper: Paper,
        *,
        stage0_status: ProcessingStatus | None = None,
        stage1_status: ProcessingStatus | None = None,
        error: str | None = None,
    ) -> "PaperSummary":
        stage_statuses = {
            ProcessingStatus.queued: (ProcessingStatus.queued, ProcessingStatus.queued),
            ProcessingStatus.running: (ProcessingStatus.running, ProcessingStatus.queued),
            ProcessingStatus.completed: (
                ProcessingStatus.completed,
                ProcessingStatus.completed,
            ),
            ProcessingStatus.partial: (ProcessingStatus.completed, ProcessingStatus.failed),
            ProcessingStatus.failed: (ProcessingStatus.failed, ProcessingStatus.queued),
        }
        fallback_stage0_status, fallback_stage1_status = stage_statuses[paper.status]
        return cls(
            id=paper.id,
            original_filename=paper.original_filename,
            status=paper.status,
            stage0_status=fallback_stage0_status if stage0_status is None else stage0_status,
            stage1_status=fallback_stage1_status if stage1_status is None else stage1_status,
            error=error,
        )


class PaperSummaryResponse(BaseModel):
    id: str
    original_filename: str
    status: ProcessingStatus
    stage0_status: ProcessingStatus
    stage1_status: ProcessingStatus
    error: str | None = None

    @classmethod
    def from_summary(cls, summary: PaperSummary) -> "PaperSummaryResponse":
        return cls(
            id=summary.id,
            original_filename=summary.original_filename,
            status=summary.status,
            stage0_status=summary.stage0_status,
            stage1_status=summary.stage1_status,
            error=summary.error,
        )


class PaperDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: UUID


class PaperResponse(BaseModel):
    id: str
    original_filename: str
    status: ProcessingStatus

    @classmethod
    def from_paper(cls, paper: Paper) -> "PaperResponse":
        return cls(
            id=paper.id,
            original_filename=paper.original_filename,
            status=paper.status,
        )


class BoundingBoxResponse(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class TextAnchorRectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=0)
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)


class TextAnchorDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=12_000)
    page_number: int = Field(ge=1)
    rects: list[TextAnchorRectRequest] = Field(min_length=1, max_length=200)
    element_id: UUID | None = None


class HighlightCreateRequest(TextAnchorDraftRequest):
    color: Literal["yellow", "green", "blue", "pink"] = "yellow"
    request_id: UUID


class HighlightUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: Literal["yellow", "green", "blue", "pink"]


class SelectionAssistRequest(TextAnchorDraftRequest):
    action: Literal["explain", "translate"]
    model_profile_id: UUID
    request_id: UUID


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    conversation_id: UUID | None = None
    model_profile_id: UUID
    request_id: UUID
    selection: TextAnchorDraftRequest | None = None

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

class CitationResponse(BaseModel):
    id: str
    kind: str
    page_number: int
    bbox: BoundingBoxResponse

    @classmethod
    def from_element(cls, element: DocumentElement) -> "CitationResponse":
        if (
            element.location_status != "located"
            or element.page_number is None
            or element.bbox is None
        ):
            raise ValueError("citation element must be located")
        return cls(
            id=element.id,
            kind=element.kind,
            page_number=element.page_number,
            bbox=BoundingBoxResponse(
                x0=element.bbox.x0,
                y0=element.bbox.y0,
                x1=element.bbox.x1,
                y1=element.bbox.y1,
            ),
        )

    @classmethod
    def from_snapshot(cls, snapshot: CitationSnapshot) -> "CitationResponse":
        return cls(
            id=snapshot.id,
            kind=snapshot.kind,
            page_number=snapshot.page_number,
            bbox=BoundingBoxResponse(
                x0=snapshot.bbox.x0,
                y0=snapshot.bbox.y0,
                x1=snapshot.bbox.x1,
                y1=snapshot.bbox.y1,
            ),
        )


class NoteReferenceResponse(BaseModel):
    note_id: str
    note_type: Literal["manual", "explanation", "translation"] | None
    page_number: int | None
    available: bool

    @classmethod
    def from_reference(cls, reference) -> "NoteReferenceResponse":
        return cls(
            note_id=reference.note_id,
            note_type=(
                None if reference.note_type is None else reference.note_type.value
            ),
            page_number=reference.page_number,
            available=reference.available,
        )


class ContextUsageResponse(BaseModel):
    used_tokens: int = Field(ge=0)
    context_length: int | None = None
    effective_limit: int | None = None
    compaction_threshold: int | None = None
    percent: float | None = None

    @classmethod
    def from_usage(cls, usage: "ContextUsage") -> "ContextUsageResponse":
        return cls(
            used_tokens=usage.used_tokens,
            context_length=usage.context_length,
            effective_limit=usage.effective_limit,
            compaction_threshold=usage.compaction_threshold,
            percent=usage.percent,
        )


class AgentMessageResponse(BaseModel):
    conversation_id: str
    message_id: str
    status: Literal["grounded", "insufficient_evidence"]
    paper_answer: str
    background_explanation: str | None
    citations: list[CitationResponse]
    model: ModelSnapshotResponse | None
    note_references: list[NoteReferenceResponse]
    context_usage: ContextUsageResponse | None = None


class ConversationMessageResponse(BaseModel):
    id: str
    role: AgentMessageRole
    content: str
    citations: list[CitationResponse]
    model: ModelSnapshotResponse | None
    note_references: list[NoteReferenceResponse]

    @classmethod
    def from_message(
        cls,
        message: ConversationMessage,
        citations: list[CitationResponse],
        note_references: list[NoteReferenceResponse],
    ) -> "ConversationMessageResponse":
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            citations=citations,
            model=(
                None
                if message.model_snapshot is None
                else ModelSnapshotResponse.from_snapshot(message.model_snapshot)
            ),
            note_references=note_references,
        )


class ConversationResponse(BaseModel):
    id: str
    paper_id: str
    messages: list[ConversationMessageResponse]

    @classmethod
    def from_conversation(
        cls,
        conversation: Conversation,
        messages: list[ConversationMessageResponse],
    ) -> "ConversationResponse":
        return cls(
            id=conversation.id,
            paper_id=conversation.paper_id,
            messages=messages,
        )


class AgentHealthResponse(BaseModel):
    status: str = "ok"


class ModelProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=80)
    base_url: HttpUrl
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    is_default: bool = False
    context_length: int | None = Field(default=None, ge=1000, le=10000000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=200000)

    @field_validator("display_name", "model_name")
    @classmethod
    def _normalize_required_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class ModelProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: HttpUrl | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    is_default: bool | None = None
    context_length: int | None = Field(default=None, ge=1000, le=10000000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=200000)

    @field_validator("display_name", "model_name")
    @classmethod
    def _normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @model_validator(mode="after")
    def _require_explicit_non_null_changes(self) -> "ModelProfilePatchRequest":
        if not self.model_fields_set:
            raise ValueError("at least one change is required")
        nullable = {"api_key", "context_length", "max_output_tokens"}
        if any(
            field_name not in nullable and getattr(self, field_name) is None
            for field_name in self.model_fields_set
        ):
            raise ValueError("profile fields cannot be null")
        return self


class ModelCapabilitiesResponse(BaseModel):
    basic_chat: bool
    structured_output: bool
    tool_calling: bool
    checked_at: datetime | None

    @classmethod
    def from_capabilities(
        cls, capabilities: "ModelCapabilities"
    ) -> "ModelCapabilitiesResponse":
        return cls(
            basic_chat=capabilities.basic_chat,
            structured_output=capabilities.structured_output,
            tool_calling=capabilities.tool_calling,
            checked_at=capabilities.checked_at,
        )


class ModelProfileResponse(BaseModel):
    id: UUID
    display_name: str
    base_url: str
    model_name: str
    enabled: bool
    is_default: bool
    revision: int
    has_api_key: bool
    api_key_mask: str | None
    context_length: int | None
    max_output_tokens: int | None
    capabilities: ModelCapabilitiesResponse
    read_only: bool

    @classmethod
    def from_view(cls, view: "ModelProfileView") -> "ModelProfileResponse":
        profile = view.profile
        return cls(
            id=UUID(profile.id),
            display_name=profile.display_name,
            base_url=profile.base_url,
            model_name=profile.model_name,
            enabled=profile.enabled,
            is_default=profile.is_default,
            revision=profile.revision,
            has_api_key=view.has_api_key,
            api_key_mask=view.api_key_mask,
            context_length=profile.context_length,
            max_output_tokens=profile.max_output_tokens,
            capabilities=ModelCapabilitiesResponse.from_capabilities(
                profile.capabilities
            ),
            read_only=view.read_only,
        )


class ModelProfileErrorResponse(BaseModel):
    code: str
    detail: str


class PageResponse(BaseModel):
    id: str
    number: int
    width: float
    height: float

    @classmethod
    def from_page(cls, page: Page) -> "PageResponse":
        return cls(id=page.id, number=page.number, width=page.width, height=page.height)


class SectionResponse(BaseModel):
    id: str
    title: str
    page_number: int | None
    order: int
    level: int = 1

    @classmethod
    def from_section(cls, section: Section) -> "SectionResponse":
        return cls(
            id=section.id,
            title=section.title,
            page_number=section.page_number,
            order=section.order,
            level=section.level,
        )


class ElementResponse(BaseModel):
    id: str
    kind: str
    text: str
    page_number: int | None
    bbox: BoundingBoxResponse | None
    section_id: str | None
    location_status: str
    order: int

    @classmethod
    def from_element(cls, element: DocumentElement) -> "ElementResponse":
        return cls(
            id=element.id,
            kind=element.kind,
            text=element.text,
            page_number=element.page_number,
            bbox=None
            if element.bbox is None
            else BoundingBoxResponse(
                x0=element.bbox.x0,
                y0=element.bbox.y0,
                x1=element.bbox.x1,
                y1=element.bbox.y1,
            ),
            section_id=element.section_id,
            location_status=element.location_status,
            order=element.order,
        )


class TextAnchorRectResponse(BaseModel):
    order: int
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_rect(cls, rect) -> "TextAnchorRectResponse":
        return cls(order=rect.order, x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)


class TextAnchorResponse(BaseModel):
    id: str
    quote: str
    page_number: int
    element_id: str | None
    rects: list[TextAnchorRectResponse]

    @classmethod
    def from_anchor(cls, anchor) -> "TextAnchorResponse":
        return cls(
            id=anchor.id,
            quote=anchor.quote,
            page_number=anchor.page_number,
            element_id=anchor.element_id,
            rects=[
                TextAnchorRectResponse.from_rect(rect) for rect in anchor.rects
            ],
        )


class HighlightResponse(BaseModel):
    id: str
    color: str
    anchor: TextAnchorResponse

    @classmethod
    def from_highlight(cls, highlight) -> "HighlightResponse":
        return cls(
            id=highlight.id,
            color=highlight.color,
            anchor=TextAnchorResponse.from_anchor(highlight.anchor),
        )


class NoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1)
    expected_updated_at: datetime | None = None


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str
    element_id: str | None = None
    page_number: int | None = None
    anchor: TextAnchorDraftRequest | None = None
    note_type: Literal["manual"] = "manual"
    request_id: UUID | None = None


class NoteResponse(BaseModel):
    id: str
    body: str
    element_id: str | None
    page_number: int | None
    note_type: Literal["manual", "explanation", "translation"]
    anchor_ids: list[str]
    model: ModelSnapshotResponse | None
    ai_generated: bool
    user_edited: bool
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_note(cls, note: Note) -> "NoteResponse":
        return cls(
            id=note.id,
            body=note.body,
            element_id=note.element_id,
            page_number=note.page_number,
            note_type=note.note_type.value,
            anchor_ids=list(note.anchor_ids),
            model=(
                None
                if note.model_snapshot is None
                else ModelSnapshotResponse.from_snapshot(note.model_snapshot)
            ),
            ai_generated=note.ai_generated,
            user_edited=note.user_edited,
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class AnnotationBundleResponse(BaseModel):
    highlights: list[HighlightResponse]
    notes: list[NoteResponse]
    anchors: list[TextAnchorResponse]


class PaperDocumentResponse(BaseModel):
    paper: PaperResponse
    pages: list[PageResponse]
    sections: list[SectionResponse]
    elements: list[ElementResponse]
    notes: list[NoteResponse]

    @classmethod
    def from_document(cls, document: PaperDocument) -> "PaperDocumentResponse":
        return cls(
            paper=PaperResponse.from_paper(document.paper),
            pages=[PageResponse.from_page(page) for page in document.pages],
            sections=[SectionResponse.from_section(section) for section in document.sections],
            elements=[ElementResponse.from_element(element) for element in document.elements],
            notes=[NoteResponse.from_note(note) for note in document.notes],
        )
