"""
Signal Detector Service
=======================
Runs statistical anomaly detection on incoming macro signals using a rolling z-score approach.

Algorithm:
  1. Fetch the last N data points for (country, indicator) from the DB
  2. Compute the rolling mean and standard deviation of those N points
  3. Compute z = (current_value - mean) / std
  4. Flag as anomaly if |z| >= threshold (default 2.0 = ~95th percentile)

This is the quant backbone of AfriSignal — think of it as a lightweight
alternative to more complex approaches like Bollinger Bands or CUSUM, but
deliberately simple enough to be explainable to a non-quant Bayse user.
"""
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.signal import MacroSignal, SignalStatus

settings = get_settings()


class SignalDetector:
    """
    Stateless service — instantiate once per request or inject as a dependency.
    All state lives in the database.
    """

    def __init__(self, threshold: float | None = None, window: int | None = None):
        self.threshold = threshold or settings.ANOMALY_ZSCORE_THRESHOLD
        self.window = window or settings.SIGNAL_HISTORY_WINDOW

    async def analyse(self, signal: MacroSignal, db: AsyncSession) -> MacroSignal:
        """
        Analyse a single signal against its historical context.
        Mutates the signal's z_score, rolling_mean, rolling_std,
        is_anomaly, and status fields in-place and returns it.
        """
        history = await self._get_history(signal, db)

        if len(history) < 3:
            # Not enough history to compute meaningful stats.
            # Mark as analysed but do not flag — we need at least a few
            # data points before we can trust the rolling stats.
            signal.status = SignalStatus.ANALYSED
            signal.z_score = None
            signal.is_anomaly = False
            return signal

        values = np.array(history, dtype=float)
        mean = float(np.mean(values))
        std = float(np.std(values, ddof=1))  # Sample std (Bessel's correction)

        signal.rolling_mean = mean
        signal.rolling_std = std

        if std == 0:
            # All historical values are identical — any new value is "anomalous"
            # but the z-score is undefined. We set 0 and do not flag.
            signal.z_score = 0.0
            signal.is_anomaly = False
        else:
            z = (signal.value - mean) / std
            signal.z_score = float(z)
            signal.is_anomaly = abs(z) >= self.threshold

        signal.status = (
            SignalStatus.TRIGGERED if signal.is_anomaly else SignalStatus.ANALYSED
        )

        return signal

    async def _get_history(
        self, signal: MacroSignal, db: AsyncSession
    ) -> list[float]:
        """
        Fetch the last `window` historical values for this (country, indicator)
        pair, excluding the signal itself (it hasn't been committed yet).
        Ordered oldest→newest so the array represents a time series.
        """
        stmt = (
            select(MacroSignal.value)
            .where(
                MacroSignal.country_code == signal.country_code,
                MacroSignal.indicator == signal.indicator,
                MacroSignal.source == signal.source,
                MacroSignal.id != signal.id,
            )
            .order_by(MacroSignal.observation_date.desc())
            .limit(self.window)
        )
        result = await db.execute(stmt)
        # Reverse so oldest is first (proper time series order for numpy)
        return list(reversed([row[0] for row in result.fetchall()]))

    def describe_anomaly(self, signal: MacroSignal) -> str:
        """
        Return a human-readable description of the anomaly for use
        in event generation prompts and WebSocket alerts.

        Example output:
          "Nigeria's inflation_rate hit 32.7% — 2.84 standard deviations
           above its 30-period rolling mean of 21.4%."
        """
        if not signal.is_anomaly or signal.z_score is None:
            return f"{signal.country_code} {signal.indicator} = {signal.value}"

        direction = "above" if signal.z_score > 0 else "below"
        return (
            f"{signal.country_code}'s {signal.indicator} hit {signal.value}"
            f"{' ' + signal.unit if signal.unit else ''} — "
            f"{abs(signal.z_score):.2f} standard deviations {direction} "
            f"its {self.window}-period rolling mean of "
            f"{signal.rolling_mean:.2f if signal.rolling_mean else 'N/A'}."
        )
