"""Transparent Harbor Credit policy scorecard (SYNTHETIC demo rules).

Given a feature dict and a list of policy rules, computes total score,
approve/decline outcome, optional rate uplift, and per-rule point contributions.
All decisions in the synthetic dataset are produced by this module so
explanations stay consistent with the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# SYNTHETIC lender constants — fictional Harbor Credit only.
LENDER_NAME = "Harbor Credit"
BASE_SCORE = 200
APPROVE_THRESHOLD = 150
BASE_RATE_PCT = 8.0
# Points of interest rate added per bureau default (LN-1107: 2 defaults → +4).
RATE_POINTS_PER_DEFAULT = 2.0
# Amount/income above this fires the primary debt-service rule (R08).
# LN-1230: borderline decline; reducing amount 15% can drop below this and flip.
AMOUNT_TO_INCOME_DEBT_SERVICE = 2.5

OPERATORS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass(frozen=True)
class RuleHit:
    """One fired (or checked) rule with the points it contributed."""

    rule_id: str
    feature: str
    operator: str
    threshold: float
    points: float
    text: str
    fired: bool
    feature_value: float | None


@dataclass(frozen=True)
class ScoreResult:
    """Full scorecard result for one application snapshot."""

    score: float
    outcome: str  # "approve" | "decline"
    rate: float
    rule_hits: tuple[RuleHit, ...]
    is_synthetic: bool = True

    @property
    def fired_points(self) -> dict[str, float]:
        return {h.rule_id: h.points for h in self.rule_hits if h.fired}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_threshold(raw: Any) -> float:
    return float(raw)


def evaluate_rule(
    rule: Mapping[str, Any], features: Mapping[str, Any]
) -> RuleHit:
    """Evaluate a single policy rule against features."""
    rule_id = str(rule["rule_id"])
    feature = str(rule["feature"])
    operator = str(rule["operator"])
    threshold = _coerce_threshold(rule["threshold"])
    points = float(rule["points"])
    text = str(rule.get("text", ""))

    if operator not in OPERATORS:
        raise ValueError(f"Unsupported operator: {operator}")

    feature_value = _as_float(features.get(feature))
    fired = False
    if feature_value is not None:
        fired = bool(OPERATORS[operator](feature_value, threshold))

    return RuleHit(
        rule_id=rule_id,
        feature=feature,
        operator=operator,
        threshold=threshold,
        points=points if fired else 0.0,
        text=text,
        fired=fired,
        feature_value=feature_value,
    )


def score(
    features: Mapping[str, Any],
    rules: Sequence[Mapping[str, Any]],
    *,
    base_score: float = BASE_SCORE,
    approve_threshold: float = APPROVE_THRESHOLD,
    base_rate: float = BASE_RATE_PCT,
) -> ScoreResult:
    """Compute score, outcome, rate and per-rule contributions.

    Rate = base_rate + RATE_POINTS_PER_DEFAULT * defaults (when present).
    Outcome is approve when score >= approve_threshold, else decline.
    """
    hits: list[RuleHit] = []
    total = float(base_score)
    for rule in rules:
        hit = evaluate_rule(rule, features)
        hits.append(hit)
        if hit.fired:
            total += float(rule["points"])

    outcome = "approve" if total >= approve_threshold else "decline"
    defaults = _as_float(features.get("defaults")) or 0.0
    rate = float(base_rate) + RATE_POINTS_PER_DEFAULT * defaults
    # Declines still carry a notional rate for table completeness; UI may hide it.
    return ScoreResult(
        score=total,
        outcome=outcome,
        rate=round(rate, 2),
        rule_hits=tuple(hits),
        is_synthetic=True,
    )


def drivers_by_points(
    result: ScoreResult, *, limit: int = 5
) -> list[RuleHit]:
    """Rank fired rules by absolute points contribution (largest impact first)."""
    fired = [h for h in result.rule_hits if h.fired and h.points != 0]
    fired.sort(key=lambda h: abs(float(h.points)), reverse=True)
    return fired[:limit]
