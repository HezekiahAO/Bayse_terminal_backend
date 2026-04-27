"""
IMF SDMX-JSON Ingestion Client
================================
Fetches African macroeconomic data from the IMF's SDMX-JSON REST API.

What is SDMX?
  SDMX (Statistical Data and Metadata eXchange) is an international standard
  for exchanging statistical data adopted by the IMF, World Bank, Eurostat,
  and most central banks. The IMF exposes its data through an SDMX-JSON REST
  API that requires no API key and is completely free.

Base URL: https://dataservices.imf.org/REST/SDMX_JSON.svc/

Key endpoint we use:
  GET /CompactData/{database_id}/{frequency}.{country_code}.{indicator_code}

  Example — monthly CPI for Nigeria:
  GET /CompactData/IFS/M.NG.PCPI_IX

  Breaking it down:
    IFS         = International Financial Statistics (the IMF database)
    M           = Monthly frequency
    NG          = Nigeria's ISO-2 country code
    PCPI_IX     = Consumer Price Index indicator code

How the response looks:
  {
    "CompactData": {
      "DataSet": {
        "Series": {
          "@FREQ": "M",
          "@REF_AREA": "NG",
          "@INDICATOR": "PCPI_IX",
          "Obs": [
            {"@TIME_PERIOD": "2024-01", "@OBS_VALUE": "482.3"},
            {"@TIME_PERIOD": "2024-02", "@OBS_VALUE": "501.7"},
            ...
          ]
        }
      }
    }
  }

Note: The IMF API returns attributes prefixed with "@" — this is SDMX convention,
not a typo. These are XML attributes serialised into JSON.

Why IMF data is better than World Bank for some use cases:
  - MONTHLY frequency (World Bank is annual for most indicators)
  - More current — updated monthly, not annually
  - Includes financial data World Bank doesn't have:
    FX rates, policy interest rates, money supply (M2), reserve assets
  - Critical for fast-moving prediction markets (FX crises, rate decisions)
"""
from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.signal import DataSource, MacroSignal, SignalStatus

settings = get_settings()

BASE_URL = "https://dataservices.imf.org/REST/SDMX_JSON.svc"

# ── African countries we track ───────────────────────────────────────────────
# ISO-2 codes used by the IMF (same as World Bank for most countries)
AFRICAN_COUNTRIES = ["NG", "KE", "ZA", "GH", "EG", "ET", "TZ", "RW", "SN", "MA"]

# ── IMF indicator codes → our internal indicator names ───────────────────────
#
# These are International Financial Statistics (IFS) indicator codes.
# Format: {database}.{frequency}.{country}.{indicator}
#
# We define indicators as (imf_code, our_name, unit, description) tuples
# so we can give good human-readable names to the data.
#
IFS_INDICATORS: list[tuple[str, str, str, str]] = [
    (
        "PCPI_IX",
        "cpi_index",
        "index",
        "Consumer Price Index — monthly. More current than World Bank annual CPI."
    ),
    (
        "ENDA_XDC_USD_RATE",
        "usd_exchange_rate",
        "local_currency_per_usd",
        "End-of-period exchange rate vs USD. Critical for FX crisis detection."
    ),
    (
        "FPOLM_PA",
        "monetary_policy_rate",
        "percent_per_annum",
        "Monetary policy (benchmark interest) rate. Set by central banks."
    ),
    (
        "FM2_XDC",
        "money_supply_m2",
        "local_currency_millions",
        "Broad money supply M2. Leading indicator for future inflation."
    ),
    (
        "RAFA_USD",
        "foreign_reserves_usd",
        "usd_millions",
        "Reserve assets in USD. Sharp declines signal FX crisis risk."
    ),
]

# IMF database and frequency prefix for IFS monthly data
IFS_DATABASE = "IFS"
FREQUENCY = "M"  # Monthly


class IMFClient:
    """
    Async HTTP client for the IMF SDMX-JSON API.

    Design notes:
      - We fetch one indicator per country per request (the API requires this)
      - We use tenacity for automatic retry with exponential backoff
        (IMF API occasionally returns 429 or 503 under load)
      - We request the last 36 months of data so the signal detector
        has enough history for meaningful rolling statistics
      - Error handling is defensive: a failure for one country/indicator
        should never stop the rest of the pipeline
    """

    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30.0,
            headers={
                "Accept": "application/json",
                "User-Agent": "AfriSignal/1.0 (prediction-market-research)",
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._http.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def fetch_indicator(
        self,
        country_code: str,
        imf_code: str,
        our_name: str,
        unit: str,
        months: int = 36,
    ) -> list[MacroSignal]:
        """
        Fetch one indicator for one country from the IMF IFS database.

        The URL pattern is:
          /CompactData/IFS/M.{country}.{indicator}?startPeriod={YYYY-MM}

        Args:
            country_code: ISO-2 country code e.g. "NG"
            imf_code:     IMF indicator code e.g. "PCPI_IX"
            our_name:     Our internal name e.g. "cpi_index"
            unit:         Human readable unit e.g. "index"
            months:       How many months of history to request

        Returns:
            List of MacroSignal objects (not yet saved to DB)
        """
        # Calculate start period (36 months ago) as "YYYY-MM"
        from dateutil.relativedelta import relativedelta
        start = datetime.now(timezone.utc) - relativedelta(months=months)
        start_period = start.strftime("%Y-%m")

        # Build the SDMX key: FREQUENCY.COUNTRY.INDICATOR
        # Example: "M.NG.PCPI_IX"
        sdmx_key = f"{FREQUENCY}.{country_code}.{imf_code}"

        url = f"/CompactData/{IFS_DATABASE}/{sdmx_key}"
        params = {"startPeriod": start_period}

        response = await self._http.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        return self._parse_response(data, country_code, imf_code, our_name, unit)

    def _parse_response(
        self,
        data: dict,
        country_code: str,
        imf_code: str,
        our_name: str,
        unit: str,
    ) -> list[MacroSignal]:
        """
        Parse the SDMX-JSON response into MacroSignal objects.

        The response structure is deeply nested. We navigate:
          data["CompactData"]["DataSet"]["Series"]["Obs"]
        to get the list of observations.

        Each observation looks like:
          {"@TIME_PERIOD": "2024-01", "@OBS_VALUE": "482.3"}

        The "@" prefix is SDMX convention (XML attributes in JSON).
        """
        signals = []

        try:
            # Navigate the deeply nested SDMX-JSON structure
            dataset = data.get("CompactData", {}).get("DataSet", {})
            series = dataset.get("Series", {})

            # "Obs" can be a list (multiple observations) or a dict (single observation)
            # We normalise both cases to a list
            obs_raw = series.get("Obs", [])
            observations = obs_raw if isinstance(obs_raw, list) else [obs_raw]

        except (AttributeError, KeyError):
            # If the structure is unexpected, return empty — don't crash the pipeline
            return []

        for obs in observations:
            time_period = obs.get("@TIME_PERIOD")  # e.g. "2024-01"
            obs_value = obs.get("@OBS_VALUE")       # e.g. "482.3" (always a string)

            # Skip observations with missing values (IMF uses blank for no data)
            if not time_period or not obs_value:
                continue

            try:
                value = float(obs_value)
            except ValueError:
                continue  # Skip non-numeric values

            # Parse "YYYY-MM" into a datetime (end of that month)
            try:
                obs_date = datetime.strptime(time_period, "%Y-%m").replace(
                    day=28,  # Use 28th to avoid month-end edge cases
                    tzinfo=timezone.utc,
                )
            except ValueError:
                # Some series use "YYYY" (annual) — handle gracefully
                try:
                    year = int(time_period)
                    obs_date = datetime(year, 12, 31, tzinfo=timezone.utc)
                except ValueError:
                    continue

            signals.append(
                MacroSignal(
                    source=DataSource.IMF,
                    country_code=country_code.upper(),
                    indicator=our_name,
                    value=value,
                    unit=unit,
                    observation_date=obs_date,
                    status=SignalStatus.RAW,
                )
            )

        # Return oldest → newest (chronological order)
        return sorted(signals, key=lambda s: s.observation_date)

    async def fetch_all_african_signals(self) -> list[MacroSignal]:
        """
        Fetch all tracked IMF indicators for all African countries.

        This is the method called by the Celery task. It iterates over
        every country × indicator combination and collects all results
        into a flat list.

        Error handling strategy:
          - A failure for one country/indicator pair is logged and skipped
          - The pipeline continues with remaining combinations
          - This prevents one bad API response from wiping out a full ingestion run
        """
        all_signals: list[MacroSignal] = []
        failed: list[str] = []

        for country in AFRICAN_COUNTRIES:
            for imf_code, our_name, unit, description in IFS_INDICATORS:
                try:
                    signals = await self.fetch_indicator(
                        country_code=country,
                        imf_code=imf_code,
                        our_name=our_name,
                        unit=unit,
                    )
                    all_signals.extend(signals)
                    print(
                        f"[IMF] {country}/{our_name}: fetched {len(signals)} observations"
                    )
                except httpx.HTTPStatusError as exc:
                    # 404 = this country doesn't have this indicator (common with IMF)
                    # 429 = rate limited (tenacity already retried 3x)
                    msg = f"{country}/{our_name} HTTP {exc.response.status_code}"
                    failed.append(msg)
                    print(f"[IMF] Skipped {msg}")
                except Exception as exc:
                    msg = f"{country}/{our_name}: {type(exc).__name__}: {exc}"
                    failed.append(msg)
                    print(f"[IMF] Error {msg}")

        print(
            f"[IMF] Done. {len(all_signals)} signals fetched. "
            f"{len(failed)} combinations skipped."
        )
        return all_signals
