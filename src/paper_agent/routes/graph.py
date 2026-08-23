from fastapi import APIRouter, HTTPException, Query, Request

from paper_agent.domain import GraphStage, ProcessingStatus
from paper_agent.schemas import (
    GraphBuildRequest,
    GraphNodeResponse,
    GraphPathsResponse,
    PaperGraphResponse,
)
from paper_agent.services.graph_construction import (
    GraphBuildConflictError,
    GraphBuildPrerequisiteError,
    GraphBuildUnavailableError,
    GraphConstructionService,
)
from paper_agent.services.model_profiles import ModelProfileNotFoundError
from paper_agent.services.reasoning_clients import (
    ReasoningClientProvider,
    ResolvedReasoningClients,
)
from paper_agent.storage import PaperRepository


router = APIRouter(prefix="/api/papers", tags=["graph"])


def _construction_service(request: Request) -> GraphConstructionService:
    return request.app.state.graph_construction_service


def _repository(request: Request) -> PaperRepository:
    return request.app.state.paper_repository


def _provider(request: Request) -> ReasoningClientProvider:
    return request.app.state.reasoning_client_provider


def _model_profile_service(request: Request):
    return request.app.state.model_profile_service


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Paper resource not found.")


def _require_paper(request: Request, paper_id: str) -> PaperRepository:
    repository = _repository(request)
    if repository.get_paper(paper_id) is None:
        raise _not_found()
    return repository


def _require_node(repository: PaperRepository, paper_id: str, node_id: str) -> None:
    if repository.get_graph_node(paper_id, node_id) is None:
        raise _not_found()


def _resolve_graph_model(
    provider: ReasoningClientProvider, profile_id: str
) -> ResolvedReasoningClients:
    try:
        resolved = provider.resolve(profile_id)
    except Exception:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from None
    capabilities = resolved.profile.capabilities
    if not provider.is_read_only_profile(resolved.profile.id) and (
        not capabilities.structured_output
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected model does not support graph construction.",
        )
    return resolved


def _build_graph(
    paper_id: str, request: Request, stage: GraphStage, payload: GraphBuildRequest
) -> PaperGraphResponse:
    repository = _require_paper(request, paper_id)
    service = _construction_service(request)
    request_id = str(payload.request_id)
    profile_id = str(payload.model_profile_id)
    durable_status = repository.get_graph_build_status(
        paper_id, stage.value, request_id
    )
    if durable_status is ProcessingStatus.completed:
        return PaperGraphResponse.from_graph(repository.get_graph(paper_id))
    if durable_status is ProcessingStatus.running:
        raise HTTPException(
            status_code=409,
            detail="Graph build is already running for this request.",
        )
    try:
        service.require_prerequisites(paper_id, stage)
        with _model_profile_service(request).usage_lease(profile_id):
            resolved = _resolve_graph_model(_provider(request), profile_id)
            if stage is GraphStage.core:
                graph = service.build_core(
                    paper_id,
                    client=resolved.structured,
                    model_snapshot=resolved.snapshot,
                    request_id=request_id,
                )
            else:
                graph = service.build_deep(
                    paper_id,
                    client=resolved.structured,
                    model_snapshot=resolved.snapshot,
                    request_id=request_id,
                )
        return PaperGraphResponse.from_graph(graph)
    except ModelProfileNotFoundError:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from None
    except GraphBuildUnavailableError as error:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from error
    except GraphBuildPrerequisiteError as error:
        raise HTTPException(
            status_code=409, detail="Paper graph prerequisites are not complete."
        ) from error
    except GraphBuildConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="Graph build is already running for this request.",
        ) from error
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500, detail="The graph could not be constructed."
        ) from error


@router.post("/{paper_id}/graph/core", response_model=PaperGraphResponse)
def build_core_graph(
    paper_id: str, payload: GraphBuildRequest, request: Request
) -> PaperGraphResponse:
    return _build_graph(paper_id, request, GraphStage.core, payload)


@router.post("/{paper_id}/graph/deep", response_model=PaperGraphResponse)
def build_deep_graph(
    paper_id: str, payload: GraphBuildRequest, request: Request
) -> PaperGraphResponse:
    return _build_graph(paper_id, request, GraphStage.deep, payload)


@router.get("/{paper_id}/graph", response_model=PaperGraphResponse)
def get_graph(paper_id: str, request: Request) -> PaperGraphResponse:
    repository = _require_paper(request, paper_id)
    return PaperGraphResponse.from_graph(repository.get_graph(paper_id))


@router.get("/{paper_id}/graph/nodes/{node_id}", response_model=GraphNodeResponse)
def get_graph_node(paper_id: str, node_id: str, request: Request) -> GraphNodeResponse:
    repository = _require_paper(request, paper_id)
    node = repository.get_graph_node(paper_id, node_id)
    if node is None:
        raise _not_found()
    return GraphNodeResponse.from_node(node)


@router.get(
    "/{paper_id}/graph/nodes/{node_id}/neighbors", response_model=PaperGraphResponse
)
def get_graph_neighbors(
    paper_id: str, node_id: str, request: Request
) -> PaperGraphResponse:
    repository = _require_paper(request, paper_id)
    _require_node(repository, paper_id, node_id)
    return PaperGraphResponse.from_graph(repository.get_graph_neighbors(paper_id, node_id))


@router.get("/{paper_id}/graph/paths", response_model=GraphPathsResponse)
def find_graph_paths(
    paper_id: str,
    request: Request,
    source_id: str = Query(min_length=1),
    target_id: str = Query(min_length=1),
    max_depth: int = Query(default=3, ge=0),
) -> GraphPathsResponse:
    repository = _require_paper(request, paper_id)
    _require_node(repository, paper_id, source_id)
    _require_node(repository, paper_id, target_id)
    return GraphPathsResponse.from_paths(
        repository.find_graph_paths(paper_id, source_id, target_id, max_depth)
    )


@router.get("/{paper_id}/graph/subgraph", response_model=PaperGraphResponse)
def get_graph_subgraph(
    paper_id: str,
    request: Request,
    node_id: str = Query(min_length=1),
    depth: int = Query(default=1, ge=0),
) -> PaperGraphResponse:
    repository = _require_paper(request, paper_id)
    _require_node(repository, paper_id, node_id)
    return PaperGraphResponse.from_graph(
        repository.get_graph_subgraph(paper_id, (node_id,), depth)
    )
