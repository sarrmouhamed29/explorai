from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from explorai_backend.api.health import router as health_router
from explorai_backend.config import get_settings
from explorai_backend.middleware import OriginCheckMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Fail fast: invalid or missing configuration aborts startup instead of
    # surfacing on the first /health/ready request. Not run by create_app().openapi().
    get_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Explorai API", lifespan=lifespan)
    app.add_middleware(OriginCheckMiddleware)
    app.include_router(health_router)
    return app


app = create_app()
