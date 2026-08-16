from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import auth as auth_router

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


# Routers are registered here as milestones land. Every data router carries the
# `current_user` dependency at router level (§4 Auth), so a new endpoint is
# authenticated by default rather than by remembering to add it. `auth` is the
# deliberate exception — see app/routers/__init__.py and app/routers/auth.py.
app.include_router(auth_router.router, prefix="/api")
