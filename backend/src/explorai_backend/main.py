from fastapi import FastAPI

from explorai_backend.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="Explorai API")
    app.include_router(health_router)
    return app


app = create_app()
