"""Every router in this package (except `auth`) declares `current_user` as a
*router-level* dependency, not a per-endpoint one:

    from fastapi import APIRouter, Depends
    from app.auth.deps import current_user

    router = APIRouter(
        prefix="/races", tags=["races"], dependencies=[Depends(current_user)]
    )

This is the whole convention (§4 Auth by default): a new endpoint added to an
already-authenticated router is authenticated automatically, rather than by
remembering to add `Depends(current_user)` to it individually. The only routes
exempt from this are `/health` (registered directly on the app in `main.py`) and
`auth`'s own `/login`, `/logout` and `/refresh`, which must be reachable without a
session — `auth.router` applies `current_user` per-endpoint instead, only on
`/me`.
"""
