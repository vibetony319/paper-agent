from fastapi import APIRouter, HTTPException, Query, Request

from paper_agent.schemas import (
    GraphNodeResponse,
    GraphPathsResponse,
    PaperGraphResponse,
)
from paper_agent.services.graph_construction import (
    GraphBuildPrerequisiteError,
    GraphBuildUnavailableError,
    GraphConstructionService,
)
from paper_agent.storage import PaperRepository


router = APIRouter(prefix="/api/papers", tags=["graph"])


def _construction_service(request: Request) -> GraphConstructionService:
    return request.app.state.graph_construction_service


def _repository(request: Request) -> PaperRepository:
    return request.app.state.paper_repository


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


def _build_graph(paper_id: str, request: Request, stage: str) -> PaperGraphResponse:
    _require_paper(request, paper_id)
    try:
        service = _construction_service(request)
        graph = service.build_core(paper_id) if stage == "core" else service.build_deep(paper_id)
        return PaperGraphResponse.from_graph(graph)
    except GraphBuildUnavailableError as error:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from error
    except GraphBuildPrerequisiteError as error:
        raise HTTPException(
            status_code=409, detail="Paper graph prerequisites are not complete."
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=500, detail="The graph could not be constructed."
        ) from error


@router.post("/{paper_id}/graph/core", response_model=PaperGraphResponse)
def build_core_graph(paper_id: str, request: Request) -> PaperGraphResponse:
    return _build_graph(paper_id, request, "core")


@router.post("/{paper_id}/graph/deep", response_model=PaperGraphResponse)
def build_deep_graph(paper_id: str, request: Request) -> PaperGraphResponse:
    return _build_graph(paper_id, request, "deep")


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
