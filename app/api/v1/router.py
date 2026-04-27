"""
REST API v1 router for signals and events endpoints.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["signals"])


@router.get("/signals")
async def list_signals():
    """List recent macro signals."""
    return {"signals": []}


@router.get("/events")
async def list_events():
    """List generated prediction market events."""
    return {"events": []}
