from fastapi import FastAPI

from paper_agent.config import Settings, get_settings
from paper_agent.routes.health import router


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="paper-agent")
    app.state.settings = settings or get_settings()
    app.include_router(router)
    return app
