from fastapi import FastAPI

from app.api.files import router as files_router
from app.config import Settings
from app.db import Base, make_engine, make_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///"):
        db_path = settings.database_url.removeprefix("sqlite:///")
        if db_path != ":memory:":
            from pathlib import Path

            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)  # small app: no migrations tool (see README, Future scope)

    app = FastAPI(
        title="Geospatial File Measurement API",
        version="0.1.0",
        description="Upload a Shapefile (.zip), KML or KMZ and get per-feature area/length.",
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.include_router(files_router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
