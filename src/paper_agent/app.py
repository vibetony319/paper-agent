from dataclasses import replace

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.config import Settings, get_settings
from paper_agent.model_profile_storage import ModelProfileRepository
from paper_agent.routes import (
    agent_router,
    annotations_router,
    graph_router,
    health_router,
    model_profiles_router,
    papers_router,
)
from paper_agent.routes.annotations import AnnotationHttpError
from paper_agent.routes.model_profiles import ModelProfileHttpError
from paper_agent.services.agent_runtime import PaperAgentRuntime
from paper_agent.services.agent_tools import PaperToolRegistry
from paper_agent.services.annotations import AnnotationService
from paper_agent.services.citation_guard import CitationGuard
from paper_agent.services.graph_construction import GraphConstructionService
from paper_agent.services.ingestion import PaperIngestionService
from paper_agent.services.model_profiles import ModelProfileService
from paper_agent.services.model_secrets import ModelSecretStore
from paper_agent.services.reasoning_clients import ReasoningClientProvider
from paper_agent.storage import PaperRepository


class _GraphAwarePaperIngestionService(PaperIngestionService):
    def get_summary(self, paper_id: str):
        summary = super().get_summary(paper_id)
        return replace(
            summary,
            stage2_status=self.repository.get_latest_stage_status(paper_id, "stage2"),
            stage3_status=self.repository.get_latest_stage_status(paper_id, "stage3"),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="paper-agent")
    app.state.settings = settings or get_settings()
    app.state.model_profile_repository = ModelProfileRepository(
        app.state.settings.database_url
    )
    app.state.model_secret_store = ModelSecretStore(
        app.state.settings.model_secrets_path
    )
    app.state.reasoning_client_provider = ReasoningClientProvider(
        app.state.model_profile_repository,
        app.state.model_secret_store,
        app.state.settings.reasoning_model,
    )
    app.state.model_profile_service = ModelProfileService(
        app.state.model_profile_repository,
        app.state.reasoning_client_provider,
        app.state.model_secret_store,
    )
    repository = PaperRepository(app.state.settings.database_url)
    app.state.paper_repository = repository
    app.state.annotation_repository = PaperAnnotationRepository(
        engine=repository.engine
    )
    app.state.annotation_service = AnnotationService(
        app.state.annotation_repository
    )
    app.state.paper_tool_registry = PaperToolRegistry(repository)
    app.state.citation_guard = CitationGuard()
    app.state.paper_agent_runtime = PaperAgentRuntime(
        repository=repository,
        tools=app.state.paper_tool_registry,
        guard=app.state.citation_guard,
    )
    app.state.paper_ingestion_service = _GraphAwarePaperIngestionService(
        settings=app.state.settings,
        repository=repository,
    )
    app.state.graph_construction_service = GraphConstructionService(
        repository=repository
    )

    @app.exception_handler(ModelProfileHttpError)
    async def model_profile_http_error(
        _request: Request, error: ModelProfileHttpError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "detail": error.detail},
        )

    @app.exception_handler(AnnotationHttpError)
    async def annotation_http_error(
        _request: Request, error: AnnotationHttpError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "detail": error.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(
        request: Request, error: RequestValidationError
    ):
        if request.url.path.startswith("/api/model-profiles"):
            return JSONResponse(
                status_code=422,
                content={
                    "code": "validation_error",
                    "detail": "模型档案请求无效。",
                },
            )
        return await request_validation_exception_handler(request, error)

    app.include_router(health_router)
    app.include_router(papers_router)
    app.include_router(graph_router)
    app.include_router(agent_router)
    app.include_router(annotations_router)
    app.include_router(model_profiles_router)
    return app
