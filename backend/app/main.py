"""App factory: /api, /static and the page shells from one process."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import feed, pages
from app.config import get_settings
from app.routers import (
    access,
    classification,
    comparison,
    fueling,
    gear,
    imports,
    photos,
    races,
    timeline,
    trends,
    weather,
)

# Every router is built by an access factory (app.access); none is mounted bare.
ROUTERS = [
    access.anyone,
    access.read,
    access.write,
    timeline.read,
    races.read,
    races.write,
    imports.write,
    classification.read,
    classification.write,
    comparison.read,
    trends.read,
    weather.write,
    fueling.write,
    gear.write,
    photos.read,
    photos.write,
]


def create_app() -> FastAPI:
    app = FastAPI(title="race-app", docs_url="/api/docs", openapi_url="/api/openapi.json")
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(pages.router)
    app.include_router(feed.router)
    static = get_settings().frontend_dir / "static"
    static.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")
    return app


app = create_app()
