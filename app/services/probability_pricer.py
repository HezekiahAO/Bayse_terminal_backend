"""
Probability Pricer
==================
Computes the opening probability for a prediction market event using a
Bayesian Beta-distribution model.

The Beta distribution is the natural prior for probabilities — it lives on
[0, 1] and is parameterised by alpha (pseudo-successes) and beta (pseudo-failures).
The mean of Beta(α, β) = α / (α + β).

Our approach:
  1. Start with a historical base rate for this indicator type.
     (e.g. "historically, 28% of 'will inflation rise 10%+ in 6m' events resolve YES")
  2. Encode that as a Beta prior: alpha = base_rate * PRIOR_STRENGTH, beta = (1 - base_rate) * PRIOR_STRENGTH
  3. Update the prior based on signal strength (z-score):
     - Strong positive anomaly → shift alpha up (higher probability of YES)
     - Strong negative anomaly → shift beta up (lower probability of YES)
  4. Compute posterior mean = alpha / (alpha + beta) as the opening price.
  5. Compute confidence as a function of our total pseudo-count (alpha + beta).

This gives us an opening probability that:
  - Is anchored to historical base rates (not just AI vibes)
  - Updates meaningfully based on the magnitude of the detected anomaly
  - Has a quantified confidence level (tighter priors = higher confidence)
"""
import math
from dataclasses import dataclass

from scipy.stats import beta as beta_dist

# ── Base rates per indicator ─────────────────────────────────────────────────
# These represent the historical frequency with which a market framed as
# "will X worsen/escalate" resolved YES for each indicator type.
# In a production system these would be computed from a resolved-market database.
# For now, these are informed prior estimates.

BASE_RATES: dict[str, float] = {
    "inflation_rate": 0.55,        # Inflation tends to persist → slightly >50%
    "gdp_growth": 0.40,            # GDP slowdowns: slightly below coin flip
    "usd_exchange_rate": 0.50,     # Currency moves: coin flip without context
    "unemployment_rate": 0.45,     # Unemployment hard to move quickly
    "interest_rate": 0.50,         # Central bank policy: coin flip
    "current_account_balance": 0.42,
    "government_debt_gdp": 0.60,   # Debt tends to increase → higher base
    "foreign_reserves": 0.40,      # Reserves tend to decline under pressure
    "trade_balance": 0.48,
    "default": 0.50,               # Fallback for unknown indicators
}

PRIOR_STRENGTH = 10.0  # How many pseudo-observations to give the prior


@dataclass
class PricingResult:
    opening_probability: float
    beta_alpha: float
    beta_beta: float
    confidence_score: float
    reasoning: str


class ProbabilityPricer:
    """
    Stateless — no DB access needed. Pure quant maths.
    """

    def price(self, indicator: str, z_score: float) -> PricingResult:
        """
        Price a prediction market event given the indicator type and
        the z-score of the triggering signal.

        A positive z_score means the signal moved in a direction typically
        associated with worsening conditions (e.g. inflation rising, FX depreciating).
        A negative z_score means improvement.

        The caller (event generator) is responsible for correctly interpreting
        the z-score direction relative to the event question.
        """
        base_rate = BASE_RATES.get(indicator, BASE_RATES["default"])

        # ── Encode the prior ─────────────────────────────────
        # Prior: Beta(alpha_0, beta_0) with mean = base_rate
        alpha = base_rate * PRIOR_STRENGTH
        beta = (1.0 - base_rate) * PRIOR_STRENGTH

        # ── Update based on signal strength ──────────────────
        # Each unit of z-score adds `z_weight` pseudo-observations.
        # We use a sigmoid-like dampening so extreme z-scores don't
        # completely dominate the prior.
        z_weight = self._dampened_z_weight(z_score)

        if z_score > 0:
            # Anomaly in the "bad" direction → increases P(YES)
            alpha += z_weight
        else:
            # Anomaly in the "good" direction → decreases P(YES)
            beta += z_weight

        # ── Posterior mean and confidence ─────────────────────
        opening_probability = alpha / (alpha + beta)

        # Confidence: higher pseudo-count → tighter distribution → higher confidence.
        # We normalise using a logistic curve so it stays in [0, 1].
        pseudo_count = alpha + beta
        confidence = self._confidence_from_pseudo_count(pseudo_count)

        # Clip to reasonable market probability range [0.05, 0.95]
        opening_probability = max(0.05, min(0.95, opening_probability))

        reasoning = self._build_reasoning(
            indicator, base_rate, z_score, z_weight, alpha, beta, opening_probability
        )

        return PricingResult(
            opening_probability=round(opening_probability, 4),
            beta_alpha=round(alpha, 4),
            beta_beta=round(beta, 4),
            confidence_score=round(confidence, 4),
            reasoning=reasoning,
        )

    def _dampened_z_weight(self, z_score: float) -> float:
        """
        Convert a z-score into a pseudo-observation count using a
        square-root dampening function.

        z=2 → 2.83 pseudo-obs
        z=3 → 3.46 pseudo-obs
        z=5 → 4.47 pseudo-obs  (not 7.07 — we want diminishing returns)
        """
        return math.sqrt(abs(z_score)) * 2.0

    def _confidence_from_pseudo_count(self, pseudo_count: float) -> float:
        """
        Map pseudo-count to a [0, 1] confidence score.
        pseudo_count=10 (prior only) → ~0.50
        pseudo_count=15             → ~0.62
        pseudo_count=20             → ~0.73
        """
        return 1.0 / (1.0 + math.exp(-0.15 * (pseudo_count - 10.0)))

    def _build_reasoning(
        self,
        indicator: str,
        base_rate: float,
        z_score: float,
        z_weight: float,
        alpha: float,
        beta: float,
        prob: float,
    ) -> str:
        direction = "above" if z_score > 0 else "below"
        shift = "increased" if z_score > 0 else "decreased"
        return (
            f"Base rate for '{indicator}' events resolving YES: {base_rate:.0%}. "
            f"Signal is {abs(z_score):.2f}σ {direction} rolling mean, "
            f"contributing {z_weight:.2f} pseudo-observations. "
            f"This {shift} the prior probability. "
            f"Posterior Beta({alpha:.2f}, {beta:.2f}) yields "
            f"opening probability of {prob:.1%}."
        )

    def get_beta_interval(
        self, alpha: float, beta: float, credible_mass: float = 0.90
    ) -> tuple[float, float]:
        """
        Compute the highest-density credible interval for the probability.
        Returns (lower, upper) bounds.

        Useful for communicating uncertainty to Bayse users:
        "Opening price 55% (90% CI: 42% – 68%)"
        """
        lower = beta_dist.ppf((1 - credible_mass) / 2, alpha, beta)
        upper = beta_dist.ppf(1 - (1 - credible_mass) / 2, alpha, beta)
        return round(float(lower), 4), round(float(upper), 4)
