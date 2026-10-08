"""Application factory (kept separate from main.py so tests can build isolated apps)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .database import Base, make_engine, make_session_factory
from .errors import GeoFileError
from .routers import files


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.resolved_database_url)
    Base.metadata.create_all(engine)

    app = FastAPI(
        title="Geospatial File Measurement API",
        version="1.0.0",
        description=(
            "Upload a KML or zipped Shapefile, extract its features and get "
            "CRS-aware area / length measurements."
        ),
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    @app.exception_handler(GeoFileError)
    async def geo_file_error_handler(_: Request, exc: GeoFileError) -> JSONResponse:
        body = {"detail": exc.message}
        if exc.file_id:
            body["file_id"] = exc.file_id
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(files.router)
    return app
