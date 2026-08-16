from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    activities as activities_router,
)
from app.routers import (
    auth as auth_router,
)
from app.routers import (
    gear as gear_router,
)
from app.routers import (
    grouping as grouping_router,
)
from app.routers import (
    races as races_router,
)
from app.routers import (
    stats as stats_router,
)
from app.routers import (
    uploads as uploads_router,
)

app = FastAPI(title="Triathlon Racing History", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Every data router carries the `current_user` dependency at router level (§4 Auth),
# so a new endpoint is authenticated by default rather than by remembering to add it.
# `auth` is the deliberate exception — see app/routers/__init__.py and routers/auth.py.
app.include_router(auth_router.router, prefix="/api")

# Order matters: `grouping` owns the literal /races/group path, and must be registered
# before `races`, whose /races/{race_id} would otherwise capture "group" as an id and
# fail on int parsing.
app.include_router(grouping_router.router, prefix="/api")
app.include_router(races_router.router, prefix="/api")
app.include_router(uploads_router.router, prefix="/api")
app.include_router(activities_router.router, prefix="/api")
app.include_router(gear_router.router, prefix="/api")
app.include_router(stats_router.router, prefix="/api")
