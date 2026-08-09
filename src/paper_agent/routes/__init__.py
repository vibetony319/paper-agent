"""Application routes."""

from paper_agent.routes.graph import router as graph_router
from paper_agent.routes.health import router as health_router
from paper_agent.routes.papers import router as papers_router

__all__ = ["graph_router", "health_router", "papers_router"]
