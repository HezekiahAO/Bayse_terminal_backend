"""
Signal domain models and enums.
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import String, Float, DateTime, Boolean, Integer, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SignalStatus(str, Enum):
    """Signal processing status."""
    RAW = "raw"
    ANALYSED = "analysed"
    TRIGGERED = "triggered"


class DataSource(str, Enum):
    """External data source identifier."""
    WORLD_BANK = "world_bank"
    IMF = "imf"
    NEWS_API = "news_api"


class MacroSignal(Base):
    """
    ORM model for a macroeconomic signal event.
    Represents a single data point for (country, indicator, source, observation_date).
    """
    __tablename__ = "macro_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    country_code: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    indicator: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[DataSource] = mapped_column(
        SQLEnum(DataSource), nullable=False, default=DataSource.WORLD_BANK
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observation_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[SignalStatus] = mapped_column(
        SQLEnum(SignalStatus), nullable=False, default=SignalStatus.RAW
    )

    # Anomaly detection results
    z_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rolling_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    rolling_std: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<MacroSignal("
            f"country_code={self.country_code}, "
            f"indicator={self.indicator}, "
            f"value={self.value}, "
            f"status={self.status})>"
        )
