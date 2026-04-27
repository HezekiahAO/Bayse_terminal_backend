"""
Event domain models for prediction market events.
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import String, Float, DateTime, Integer, Boolean, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EventStatus(str, Enum):
    """Prediction event status."""
    DRAFT = "draft"
    ACTIVE = "active"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class PredictionEvent(Base):
    """
    ORM model for a prediction market event.
    Auto-generated from anomalous macro signals.
    """
    __tablename__ = "prediction_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_signal_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    status: Mapped[EventStatus] = mapped_column(
        SQLEnum(EventStatus), nullable=False, default=EventStatus.DRAFT
    )
    opening_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<PredictionEvent("
            f"id={self.id}, "
            f"status={self.status}, "
            f"opening_probability={self.opening_probability})>"
        )
