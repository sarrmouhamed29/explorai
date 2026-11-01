import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from explorai_backend.api.auth import router as auth_router
from explorai_backend.api.documents import router as documents_router
from explorai_backend.api.health import router as health_router
from explorai_backend.config import get_settings
from explorai_backend.middleware import OriginCheckMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Fail fast: invalid or missing configuration aborts startup instead of
    # surfacing on the first /health/ready request. Not run by create_app().openapi().
    get_settings()
    yield


async def validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's 422 answer without the rejected ``input``.

    The default echoes each invalid value back, which for a too short or too
    long password would put the password in the response body.
    """
    assert isinstance(exc, RequestValidationError)
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    return JSONResponse(
        {"detail": jsonable_encoder(errors)},
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


async def database_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    # The exception text is not logged: it can carry the DSN or the statement.
    logger.warning("database unavailable")
    return JSONResponse(
        {"detail": "database unavailable"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Explorai API", lifespan=lifespan)
    app.add_middleware(OriginCheckMiddleware)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(OperationalError, database_unavailable)
    # Every pooled connection busy for pool_timeout seconds: same answer, the
    # database cannot serve this request now.
    app.add_exception_handler(PoolTimeout, database_unavailable)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(documents_router)
    return app


app = create_app()
