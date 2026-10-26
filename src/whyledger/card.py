"""WhyCard pydantic models and builder (verified claims only)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from whyledger.ledger import Claim, Ledger
from whyledger.reason_check import ReasonCheck, check_recorded_reason
from whyledger.verifier import VerificationResult
from whyledger.zetaris import ZetarisClient

Mode = Literal["live", "replay", "stub"]

# Applicant letter must stay neutral; these substrings are banned (case-insensitive).
_BANNED_LETTER_WORDS = ("legally", "compliant", "guarantee")


class Driver(BaseModel):
    """Ranked feature driver shown on the card."""

    model_config = ConfigDict(extra="forbid")

    rank: int
    feature: str
    weight_pct: float
    claim_id: str | None = None


def aggregate_drivers_by_feature(
    drivers: list[Driver], *, limit: int = 5
) -> list[Driver]:
    """Merge duplicate features (e.g. multiple amount_to_income rules) with honest weights."""
    if not drivers:
        return []
    weights: dict[str, float] = {}
    claim_for: dict[str, str | None] = {}
    for d in drivers:
        weights[d.feature] = weights.get(d.feature, 0.0) + float(d.weight_pct)
        if d.claim_id and claim_for.get(d.feature) is None:
            claim_for[d.feature] = d.claim_id
    ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)[:limit]
    out: list[Driver] = []
    for rank, (feature, weight_pct) in enumerate(ranked, start=1):
        out.append(
            Driver(
                rank=rank,
                feature=feature,
                weight_pct=round(weight_pct, 1),
                claim_id=claim_for.get(feature),
            )
        )
    return out


class CardClaim(BaseModel):
    """A claim that shipped on the card (always VERIFIED)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    status: Literal["VERIFIED"] = "VERIFIED"
    query_ids: list[str] = Field(default_factory=list)
    row_hash: str | None = None


class RejectedClaim(BaseModel):
    """Draft claim removed from the card with a verifier reason."""

    model_config = ConfigDict(extra="forbid")

    text: str
    reason: str


class Counterfactual(BaseModel):
    """Smallest described change that flips the outcome."""

    model_config = ConfigDict(extra="forbid")

    change: str
    flips_to: str


class WhyCard(BaseModel):
    """Final Why-Card JSON per docs/SPEC.md. ``synthetic`` is always true."""

    model_config = ConfigDict(extra="forbid")

    decision: str
    mode: Mode
    drivers: list[Driver] = Field(default_factory=list)
    claims: list[CardClaim] = Field(default_factory=list)
    rejected: list[RejectedClaim] = Field(default_factory=list)
    counterfactual: Counterfactual | None = None
    reason_check: ReasonCheck | None = None
    applicant_letter: str
    verified_at: str
    model: str
    synthetic: Literal[True] = True

    @field_validator("synthetic")
    @classmethod
    def _synthetic_always_true(cls, v: bool) -> Literal[True]:
        return True

    @field_validator("applicant_letter")
    @classmethod
    def _letter_neutral(cls, v: str) -> str:
        lower = v.lower()
        for word in _BANNED_LETTER_WORDS:
            if word in lower:
                raise ValueError(
                    f"applicant_letter must not contain banned word {word!r}"
                )
        return v


_FEATURE_LABELS: dict[str, str] = {
    "amount_to_income": "Amount relative to declared income",
    "utilisation": "Credit utilisation",
    "late_over_15_of_last_6": "Late payments on prior loan (over 15 days)",
    "defaults": "Bureau defaults",
    "bureau_score": "Bureau score",
    "enquiries_6m": "Credit enquiries (last 6 months)",
    "years_in_business": "Years in business",
    "declared_income": "Declared income",
    "amount": "Loan amount",
    "sector_risk": "Sector risk",
}


def _feature_label(feature: str) -> str:
    return _FEATURE_LABELS.get(feature, feature.replace("_", " "))


def _claim_matches_feature(feature: str, text: str) -> bool:
    """True when claim text describes this scorecard feature (not a mismatched id)."""
    lower = text.lower()
    if feature == "bureau_score":
        if "bureau score" not in lower:
            return False
        return "scorecard points" not in lower or "bureau score" in lower
    if feature == "amount_to_income":
        if "scorecard points" in lower or "outcome is" in lower:
            return False
        return (
            "amount to income" in lower
            or "times" in lower and "income" in lower
            or "ratio" in lower and "income" in lower
        )
    if feature == "utilisation":
        return "utilisation" in lower or "utilization" in lower
    if feature == "enquiries_6m":
        return "enquir" in lower
    if feature == "defaults":
        return "default" in lower and "reason code" not in lower
    if feature == "late_over_15_of_last_6":
        return "late" in lower and "15" in lower
    return True


def _claim_text_for_driver(driver: Driver, claims: list[CardClaim]) -> str | None:
    if driver.claim_id:
        for c in claims:
            if c.id == driver.claim_id:
                if _claim_matches_feature(driver.feature, c.text):
                    return c.text
                break
    feat = driver.feature
    hints: dict[str, tuple[str, ...]] = {
        "late_over_15_of_last_6": ("late", "15 days"),
        "amount_to_income": ("amount to income", "times declared"),
        "utilisation": ("utilisation", "util"),
        "defaults": ("defaults", "default"),
        "bureau_score": ("bureau score",),
        "enquiries_6m": ("enquir",),
    }
    keys = hints.get(feat, (feat.replace("_", " "),))
    for c in claims:
        blob = c.text.lower()
        if any(k in blob for k in keys) and _claim_matches_feature(feat, c.text):
            return c.text
    return None


def select_letter_drivers(drivers: list[Driver]) -> list[Driver]:
    """Drivers for the letter: weight >= 15%, at least top 2, max 3, rank order."""
    merged = aggregate_drivers_by_feature(drivers)
    if not merged:
        return []
    keep: set[int] = set(range(min(2, len(merged))))
    for i, d in enumerate(merged):
        if d.weight_pct >= 15.0:
            keep.add(i)
    return [merged[i] for i in sorted(keep)[:3]]


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _derive_driver_value_from_ledger(
    feature: str, ledger: Ledger
) -> str | None:
    """Plain-language factor detail from verified SQL rows (no drafted claim)."""
    bureau_score: float | None = None
    scorecard_points: float | None = None
    for ev in ledger.evidence_map.values():
        for row in ev.rows:
            if feature == "amount_to_income":
                amt = _as_float(row.get("amount"))
                inc = _as_float(row.get("declared_income"))
                if amt is not None and inc is not None and inc > 0:
                    ratio = round(amt / inc, 1)
                    return f"requested amount is {ratio} times declared income"
            if feature == "enquiries_6m" and "enquiries_6m" in row:
                n = _as_float(row.get("enquiries_6m"))
                if n is not None:
                    count = int(n)
                    return f"{count} credit enquiries in the last 6 months"
            if feature == "utilisation" and "utilisation" in row:
                u = _as_float(row.get("utilisation"))
                if u is not None:
                    return f"credit utilisation is {int(u)} percent"
            if feature == "defaults" and "defaults" in row:
                n = _as_float(row.get("defaults"))
                if n is not None:
                    count = int(n)
                    label = "default" if count == 1 else "defaults"
                    return f"{count} bureau {label} on file"
            if feature == "late_over_15_of_last_6" and "late_count" in row:
                n = _as_float(row.get("late_count"))
                if n is not None:
                    return f"{int(n)} late payments over 15 days in the last 6"
            if feature == "years_in_business" and "years_in_business" in row:
                if "amount" in row or "declared_income" in row:
                    y = _as_float(row.get("years_in_business"))
                    if y is not None:
                        ytxt = int(y) if y == int(y) else y
                        return f"years in business is {ytxt}"
            if "bureau_score" in row:
                bureau_score = _as_float(row.get("bureau_score"))
            elif "score" in row and (
                "utilisation" in row or "enquiries_6m" in row or "defaults" in row
            ):
                bureau_score = _as_float(row.get("score"))
            if "scorecard_points" in row:
                scorecard_points = _as_float(row.get("scorecard_points"))
    if feature == "bureau_score" and bureau_score is not None:
        b = int(bureau_score)
        if scorecard_points is not None:
            pts = int(scorecard_points) if scorecard_points == int(scorecard_points) else scorecard_points
            return f"bureau score is {b}; scorecard points {pts}"
        return f"bureau score is {b}"
    return None


def _is_positive_supporting_claim(text: str) -> bool:
    lower = text.lower()
    if "outcome is" in lower or "reason code" in lower or "reason_code" in lower:
        return True
    if "peer cases" in lower and "approve" in lower:
        return True
    if "no late" in lower or "conduct has no late" in lower:
        return True
    if "scorecard rule" in lower and "adding" in lower:
        return True
    return False


def render_applicant_letter(
    *,
    loan_id: str,
    decision: str,
    drivers: list[Driver] | None = None,
    claims: list[CardClaim] | None = None,
    reason_check: ReasonCheck | None = None,
    driver_summaries: list[str] | None = None,
    ledger: Ledger | None = None,
) -> str:
    """Principal-reasons style notice (illustrative, SYNTHETIC, neutral wording)."""
    dec = (decision or "").strip().lower()
    is_decline = dec == "decline"
    section_title = (
        "Principal reasons" if is_decline else "Main factors in the decision"
    )

    lines = [
        "Illustrative explanation, not legal advice. SYNTHETIC demo data.",
        "",
        f"Regarding application {loan_id}.",
        f"The recorded decision is: {decision}.",
        "",
    ]

    if reason_check and reason_check.status == "UNSUPPORTED":
        detail = (reason_check.detail or "").strip()
        lines.append(
            "Note: the recorded reason on the decision file was not supported by "
            f"the re-checked data ({detail}). Data-supported factors are listed below."
        )
        lines.append("")

    evidence_map: dict[str, str] = {}
    evidence_order: list[str] = []
    factor_lines: list[str] = []

    card_claims = list(claims or [])
    selected = select_letter_drivers(list(drivers or []))

    if selected:
        for driver in selected:
            value_text: str | None = None
            claim_text = (
                _claim_text_for_driver(driver, card_claims) if card_claims else None
            )
            if claim_text and not _is_positive_supporting_claim(claim_text):
                value_text = claim_text
            elif ledger is not None:
                value_text = _derive_driver_value_from_ledger(driver.feature, ledger)
            label = _feature_label(driver.feature)
            weight = f" ({driver.weight_pct}% of scorecard driver weight)"
            if value_text:
                chip_key = value_text
                if chip_key not in evidence_map:
                    tag = f"E{len(evidence_order) + 1}"
                    evidence_map[chip_key] = tag
                    evidence_order.append(chip_key)
                chip = evidence_map[chip_key]
                factor_lines.append(
                    f"- {label}{weight}: {value_text} [{chip}]"
                )
            else:
                factor_lines.append(
                    f"- {label}{weight}: (from scorecard points)"
                )
    elif driver_summaries:
        for item in driver_summaries:
            if _is_positive_supporting_claim(item):
                continue
            factor_lines.append(f"- {item}")

    lines.append(section_title + ":")
    if factor_lines:
        lines.extend(factor_lines)
    else:
        lines.append(
            "- See the verified claims on the Why-Card for this SYNTHETIC case."
        )

    if evidence_order:
        lines.append("")
        parts = [f"{evidence_map[t]}: {t}" for t in evidence_order]
        lines.append("Evidence: " + "; ".join(parts) + ".")

    lines.append("")
    lines.append(
        "This notice summarises values re-checked against linked query results. "
        "It is not advice and not a commitment about future applications."
    )
    letter = "\n".join(lines)
    lower = letter.lower()
    for word in _BANNED_LETTER_WORDS:
        if word in lower:
            raise RuntimeError(f"letter template produced banned word {word!r}")
    return letter


def build_card(
    *,
    decision: str,
    mode: Mode,
    claims: list[Claim],
    results: list[VerificationResult],
    ledger: Ledger,
    drivers: list[Driver] | None = None,
    counterfactual: Counterfactual | None = None,
    reason_check: ReasonCheck | None = None,
    loan_id: str = "",
    model: str = "stub",
    verified_at: str | None = None,
    applicant_letter: str | None = None,
) -> WhyCard:
    """Build a WhyCard: only VERIFIED claims in ``claims``; others in ``rejected``."""
    by_id = {r.claim_id: r for r in results}
    verified: list[CardClaim] = []
    rejected: list[RejectedClaim] = []

    for claim in claims:
        outcome = by_id.get(claim.id)
        if outcome is None:
            rejected.append(
                RejectedClaim(text=claim.text, reason="missing verification result")
            )
            continue
        if outcome.status == "VERIFIED":
            hashes = [
                ev.sha256
                for ev in ledger.evidence_for_claim(claim)
            ]
            row_hash = hashes[0] if len(hashes) == 1 else (",".join(hashes) or None)
            verified.append(
                CardClaim(
                    id=claim.id,
                    text=claim.text,
                    status="VERIFIED",
                    query_ids=list(claim.query_ids),
                    row_hash=row_hash,
                )
            )
        else:
            rejected.append(
                RejectedClaim(text=claim.text, reason=outcome.reason)
            )

    merged_drivers = aggregate_drivers_by_feature(list(drivers or []))
    summaries = [c.text for c in verified]
    letter = applicant_letter or render_applicant_letter(
        loan_id=loan_id or "unknown",
        decision=decision,
        drivers=merged_drivers,
        claims=verified,
        reason_check=reason_check,
        driver_summaries=summaries,
        ledger=ledger,
    )
    when = verified_at or datetime.now(timezone.utc).isoformat()

    return WhyCard(
        decision=decision,
        mode=mode,
        drivers=merged_drivers,
        claims=verified,
        rejected=rejected,
        counterfactual=counterfactual,
        reason_check=reason_check,
        applicant_letter=letter,
        verified_at=when,
        model=model,
        synthetic=True,
    )


def enrich_why_card(
    card: WhyCard,
    *,
    client: ZetarisClient,
    loan_id: str,
    ledger: Ledger | None = None,
    counterfactual: Counterfactual | None = None,
) -> WhyCard:
    """Attach reason check, counterfactual, and principal-reasons letter (deterministic)."""
    from whyledger.counterfactual import find_counterfactual

    reason = check_recorded_reason(client, loan_id, ledger=ledger)
    cf = counterfactual if counterfactual is not None else card.counterfactual
    if (decision := card.decision) == "decline" and cf is None:
        search = find_counterfactual(client, loan_id)
        if search is not None:
            cf = search.counterfactual
            if ledger is not None:
                for qr in search.queries:
                    ledger.append_evidence(
                        query_id=qr.query_id,
                        sql=qr.sql,
                        columns=qr.columns,
                        rows=qr.rows,
                    )

    letter = render_applicant_letter(
        loan_id=loan_id,
        decision=decision,
        drivers=card.drivers,
        claims=card.claims,
        reason_check=reason,
        ledger=ledger,
    )
    data = card.model_dump()
    data["reason_check"] = reason.model_dump()
    data["counterfactual"] = cf.model_dump() if cf else None
    data["applicant_letter"] = letter
    return WhyCard.model_validate(data)
