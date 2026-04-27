"""
Event Generator Service
=======================
Uses Claude to draft a well-formed Bayse prediction market event from
a detected macro anomaly.

Responsibilities:
  1. Build a rich prompt from the signal + anomaly description
  2. Call the Anthropic API and parse structured JSON output
  3. Combine the AI-drafted text with the quant-priced probability
  4. Persist the PredictionEvent to the database
  5. Publish a WebSocket notification via Redis pub/sub
"""
import json
from datetime import date, timedelta

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.event import EventStatus, PredictionEvent
from app.models.signal import MacroSignal
from app.services.probability_pricer import ProbabilityPricer
from app.services.signal_detector import SignalDetector

settings = get_settings()
_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

MODEL = "claude-sonnet-4-20250514"

# ── African country metadata for richer prompts ──────────────────────────────
COUNTRY_CONTEXT: dict[str, str] = {
    "NG": "Nigeria (West Africa's largest economy, oil-dependent, floating Naira since 2023)",
    "KE": "Kenya (East Africa's tech hub, service-oriented economy, Shilling currency)",
    "ZA": "South Africa (most industrialised African economy, Rand currency, load-shedding concerns)",
    "GH": "Ghana (Cedi currency, cocoa and gold exporter, completed IMF bailout 2023)",
    "EG": "Egypt (largest North African economy, Pound currency, tourism-dependent)",
    "ET": "Ethiopia (second most populous African country, Birr currency, commodity exporter)",
    "TZ": "Tanzania (East African emerging market, Shilling currency, mining and tourism)",
    "RW": "Rwanda (fastest-growing African economy, Franc currency, tech investment hub)",
    "SN": "Senegal (West African Franc zone, oil production beginning 2024)",
    "MA": "Morocco (North Africa, Dirham currency, phosphate exporter, growing manufacturing)",
}


class EventGenerator:
    def __init__(self):
        self.pricer = ProbabilityPricer()
        self.detector = SignalDetector()

    async def generate(
        self, signal: MacroSignal, db: AsyncSession
    ) -> PredictionEvent:
        """
        Full pipeline: signal → AI draft + quant price → persisted PredictionEvent.
        """
        # Step 1: Quant-price the opening probability
        pricing = self.pricer.price(signal.indicator, signal.z_score or 0.0)

        # Step 2: Call Claude for the event text
        ai_output = await self._call_claude(signal, pricing.opening_probability)

        # Step 3: Determine resolution target date (default: 6 months out)
        target_date = date.today() + timedelta(days=180)

        # Step 4: Persist
        event = PredictionEvent(
            source_signal_id=signal.id,
            title=ai_output["title"],
            description=ai_output["description"],
            resolution_criteria=ai_output["resolution_criteria"],
            opening_probability=pricing.opening_probability,
            beta_alpha=pricing.beta_alpha,
            beta_beta=pricing.beta_beta,
            confidence_score=pricing.confidence_score,
            country_code=signal.country_code,
            indicator=signal.indicator,
            target_date=target_date,
            status=EventStatus.DRAFT,
            ai_reasoning=pricing.reasoning + "\n\n" + ai_output.get("ai_reasoning", ""),
            model_version=MODEL,
        )

        db.add(event)
        await db.flush()  # Gets the ID without committing

        return event

    async def _call_claude(
        self, signal: MacroSignal, opening_probability: float
    ) -> dict:
        """
        Call the Claude API with a structured prompt and parse JSON output.
        """
        country_ctx = COUNTRY_CONTEXT.get(
            signal.country_code,
            f"{signal.country_code} (African country)"
        )
        anomaly_desc = self.detector.describe_anomaly(signal)

        prompt = self._build_prompt(signal, anomaly_desc, country_ctx, opening_probability)

        response = _client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=self._system_prompt(),
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text
        return self._parse_json_response(raw)

    def _system_prompt(self) -> str:
        return """You are a prediction market analyst specialising in African macroeconomics.
Your job is to turn raw economic data anomalies into clear, tradeable prediction market
questions for the Bayse prediction market platform.

You MUST respond with a single JSON object and nothing else — no preamble, no markdown.
The JSON must have exactly these keys:
  - title: str (the market question, max 200 chars, must end with "?")
  - description: str (2-3 sentence context paragraph, max 400 chars)
  - resolution_criteria: str (precise, measurable, unambiguous YES conditions, max 300 chars)
  - ai_reasoning: str (your brief reasoning for the framing, max 300 chars)

Rules for good prediction markets:
1. The question must be answerable YES or NO by a specific date
2. Resolution criteria must reference a specific, publicly verifiable data source
3. Avoid compound questions (no "and/or")
4. Frame positively (ask "Will X exceed Y?" not "Will X fail to stay below Y?")
5. Be specific about the threshold (not "increase significantly" but "exceed 35%")"""

    def _build_prompt(
        self,
        signal: MacroSignal,
        anomaly_desc: str,
        country_ctx: str,
        prob: float,
    ) -> str:
        return f"""A macro economic anomaly has been detected. Draft a Bayse prediction market event.

ANOMALY DETECTED:
{anomaly_desc}

COUNTRY CONTEXT:
{country_ctx}

INDICATOR: {signal.indicator}
CURRENT VALUE: {signal.value} {signal.unit or ""}
OBSERVATION DATE: {signal.observation_date.strftime("%B %Y")}
QUANT MODEL OPENING PROBABILITY: {prob:.1%}
(This means the quant model estimates ~{prob:.0%} chance this trend continues/worsens)

Draft a prediction market question that a trader on Bayse could bet on.
The resolution date should be approximately 6 months from {signal.observation_date.strftime("%B %Y")}.
Make the question specific to {signal.country_code} and this indicator."""

    def _parse_json_response(self, raw: str) -> dict:
        """
        Safely parse Claude's JSON response.
        Strips any accidental markdown code fences.
        """
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])  # Strip first and last fence lines

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            # Fallback: return a minimal valid structure
            return {
                "title": "Will this economic indicator worsen over the next 6 months?",
                "description": f"An anomaly was detected in {raw[:100]}",
                "resolution_criteria": "Resolves YES if the indicator value exceeds the current anomalous level.",
                "ai_reasoning": f"JSON parse error — raw output used. Error: {exc}",
            }

        # Validate required keys are present
        required = {"title", "description", "resolution_criteria"}
        if not required.issubset(data.keys()):
            missing = required - data.keys()
            data["ai_reasoning"] = f"Warning: missing keys {missing}"

        return data
