"""
World Bank ingestion client.

Fetches real economic data from the World Bank API.
"""

import asyncio
import json
from datetime import date
from typing import List

import httpx
from sqlalchemy import select

from app.models.signal import MacroSignal


class WorldBankClient:
    """Client for fetching World Bank economic indicators."""

    BASE_URL = "https://api.worldbank.org/v2"

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def fetch_indicator(self, country_code: str, indicator_code: str) -> List[MacroSignal]:
        """
        Fetch a specific indicator for a country from World Bank API.

        Args:
            country_code: ISO 2-letter country code (e.g., 'NG', 'KE')
            indicator_code: World Bank indicator code (e.g., 'FP.CPI.TOTL.ZG')

        Returns:
            List of MacroSignal objects with the fetched data
        """
        url = f"{self.BASE_URL}/country/{country_code}/indicator/{indicator_code}"
        params = {
            "format": "json",
            "per_page": 1000,  # Get as many as possible
            "date": "2000:2023"  # Last 20+ years
        }

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if not data or len(data) < 2:
                return []

            # World Bank API returns [metadata, data_array]
            metadata = data[0]
            records = data[1]

            signals = []
            for record in records:
                if record.get("value") is None:
                    continue  # Skip missing values

                # Parse the date (World Bank uses YYYY format for annual data)
                try:
                    obs_date = date(int(record["date"]), 1, 1)  # January 1st of the year
                except (ValueError, KeyError):
                    continue

                # Map indicator codes to our internal names
                indicator_name = self._map_indicator_code(indicator_code)

                signal = MacroSignal(
                    country_code=country_code,
                    indicator=indicator_name,
                    source="world_bank",
                    observation_date=obs_date,
                    value=float(record["value"])
                )
                signals.append(signal)

            return signals

        except Exception as e:
            print(f"[WorldBank] Error fetching {country_code}/{indicator_code}: {e}")
            return []

    def _map_indicator_code(self, code: str) -> str:
        """Map World Bank indicator codes to our internal names."""
        mapping = {
            "FP.CPI.TOTL.ZG": "inflation_rate",
            "NY.GDP.MKTP.KD.ZG": "gdp_growth",
            "SL.UEM.TOTL.ZS": "unemployment_rate"
        }
        return mapping.get(code, code)  # Fallback to code if not mapped

    async def fetch_all_african_signals(self) -> List[MacroSignal]:
        """Fetch all configured indicators for African countries."""
        countries = ["NG", "KE", "ZA", "GH", "EG", "TZ", "UG", "MA", "TN", "SN"]
        indicators = [
            ("FP.CPI.TOTL.ZG", "inflation_rate"),
            ("NY.GDP.MKTP.KD.ZG", "gdp_growth"),
            ("SL.UEM.TOTL.ZS", "unemployment_rate")
        ]

        all_signals = []
        for country in countries:
            for wb_code, name in indicators:
                signals = await self.fetch_indicator(country, wb_code)
                all_signals.extend(signals)
                await asyncio.sleep(0.1)  # Rate limiting

        return all_signals
