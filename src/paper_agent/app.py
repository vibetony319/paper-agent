from dataclasses import replace

from fastapi import FastAPI

from paper_agent.config import Settings, get_settings
from paper_agent.models.vllm import VllmStructuredClient
from paper_agent.routes import graph_router, health_router, papers_router
from paper_agent.services.graph_construction import GraphConstructionService
from paper_agent.services.ingestion import PaperIngestionService
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
    repository = PaperRepository(app.state.settings.database_url)
    app.state.paper_repository = repository
    app.state.paper_ingestion_service = _GraphAwarePaperIngestionService(
        settings=app.state.settings,
        repository=repository,
    )
    app.state.graph_construction_service = GraphConstructionService(
        repository=repository,
        client=(
            None
            if app.state.settings.reasoning_model is None
            else VllmStructuredClient(app.state.settings.reasoning_model)
        ),
    )
    app.include_router(health_router)
    app.include_router(papers_router)
    app.include_router(graph_router)
    return app
