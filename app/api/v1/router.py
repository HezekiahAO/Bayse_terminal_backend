"""
My REST API v1 router for signals and events endpoints.
"""
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.event import PredictionEvent
from app.models.signal import MacroSignal

router = APIRouter(prefix="/api/v1", tags=["signals"])


def signal_to_dict(signal: MacroSignal) -> dict[str, Any]:
    return {
        "id": str(signal.id),
        "country_code": signal.country_code,
        "indicator": signal.indicator,
        "source": signal.source,
        "value": signal.value,
        "unit": signal.unit,
        "observation_date": signal.observation_date,
        "status": signal.status,
        "z_score": signal.z_score,
        "rolling_mean": signal.rolling_mean,
        "rolling_std": signal.rolling_std,
        "is_anomaly": signal.is_anomaly,
        "raw_data": signal.raw_data,
        "created_at": signal.created_at,
        "fetched_at": signal.fetched_at,
    }


def event_to_dict(event: PredictionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "source_signal_id": event.source_signal_id,
        "status": event.status,
        "opening_probability": event.opening_probability,
        "resolution_date": event.resolution_date,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


@router.get("/signals")
async def list_signals(
    anomalies_only: bool = Query(False, alias="anomalies_only"),
    country_code: str | None = Query(None, alias="country_code"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List recent macro signals."""
    query = select(MacroSignal).order_by(MacroSignal.observation_date.desc())

    if anomalies_only:
        query = query.where(MacroSignal.is_anomaly.is_(True))

    if country_code:
        query = query.where(MacroSignal.country_code == country_code.upper())

    query = query.limit(limit)

    result = await db.execute(query)
    signals = result.scalars().all()
    return {"signals": [signal_to_dict(signal) for signal in signals]}


@router.get("/events")
async def list_events(
    status: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List generated prediction market events."""
    query = select(PredictionEvent).order_by(PredictionEvent.created_at.desc())

    if status:
        query = query.where(PredictionEvent.status == status)

    query = query.limit(limit)

    result = await db.execute(query)
    events = result.scalars().all()
    return {"events": [event_to_dict(event) for event in events]}
