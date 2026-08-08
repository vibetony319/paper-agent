from fastapi import FastAPI

from paper_agent.config import Settings, get_settings
from paper_agent.routes.health import router
from paper_agent.routes.papers import router as papers_router
from paper_agent.services.ingestion import PaperIngestionService
from paper_agent.storage import PaperRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="paper-agent")
    app.state.settings = settings or get_settings()
    app.state.paper_ingestion_service = PaperIngestionService(
        settings=app.state.settings,
        repository=PaperRepository(app.state.settings.database_url),
    )
    app.include_router(router)
    app.include_router(papers_router)
    return app
