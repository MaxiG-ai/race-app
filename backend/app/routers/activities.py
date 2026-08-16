"""Stub — endpoints land with their milestone. See app/routers/__init__.py for the
router-level auth convention.
"""

from fastapi import APIRouter, Depends

from app.auth.deps import current_user

router = APIRouter(prefix="/activities", tags=["activities"], dependencies=[Depends(current_user)])
