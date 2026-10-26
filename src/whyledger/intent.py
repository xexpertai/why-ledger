"""Meterless Scout Intent (subset) for Why-Ledger request routing.

Spec sources (Apache 2.0, github.com/Meterless/Meterless engines/scout-intent):
- ``AGENTS.md``: five-stage pipeline (Sense → Interpret → Guard → Route → Recommend),
  confidence bands, clarification rules, execution-contract signing.
- ``docs/scoring.md``: Stage-1 lexical scoring weights (0.40 lexical; semantic/contextual 0
  without Stage-2), band thresholds (High ≥0.80, Medium 0.55–0.79, Low 0.40–0.54, Reject <0.40).
- ``docs/risk-and-policy.md``: injection pattern guard, PII/sensitivity, block aggregation.
- ``docs/execution-contract.md``: HMAC-SHA256 over canonical JSON (contract without signature).
- ``docs/intent-registry.md``: intent record shape, mandatory examples, riskClass floor.

Adapted for this product:
- Registry intents are Why-Ledger analyst tasks (``explain_decision``, ``explain_price_change``,
  ``compare_cases``, ``counterfactual``, ``out_of_scope``, ``risky``) instead of CRM/deal intents.
- Capability graph maps abstract capabilities to agent tools: ``sql``, ``policy_lookup``,
  ``peer_cases``, ``counterfactual`` (no Swarm/Markovian downstream engines).
- Stage-2 embedding rescoring is omitted (Stage-1 only); recency term is always 0.
- Contract TTL 5 minutes; ``contractId`` uses UUID4 (spec suggests ULID).
- Signing secret defaults to a local dev constant; override via ``WHYLEDGER_SCOUT_SECRET`` env.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

IntentId = Literal[
    "explain_decision",
    "explain_price_change",
    "compare_cases",
    "counterfactual",
    "out_of_scope",
    "risky",
    "GENERAL",
]

RiskLevel = Literal["low", "medium", "high", "block"]
ConfidenceBand = Literal["high", "medium", "low", "reject"]

REGISTRY_VERSION = 1
POLICY_PACK_VERSION = "default@1"
CONTRACT_TTL_SECONDS = 300

WEIGHT_LEXICAL = 0.40
WEIGHT_SEMANTIC = 0.35
WEIGHT_CONTEXTUAL = 0.20
WEIGHT_RECENCY = 0.05

BAND_HIGH = 0.80
BAND_MEDIUM = 0.55
BAND_LOW = 0.40

_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction-override", re.compile(r"ignore\s+(all\s+)?previous(\s+instructions)?", re.I)),
    ("instruction-override", re.compile(r"disregard\s+the\s+(rules|user)", re.I)),
    ("role-confusion", re.compile(r"</system>|<\s*system\b", re.I)),
    ("role-confusion", re.compile(r"\[INST\].*\[/INST\]", re.I)),
    ("role-confusion", re.compile(r"unrestricted\s+mode", re.I)),
    ("tool-ref", re.compile(r"internal_admin_panel|grant\s+me\s+admin", re.I)),
    ("credentials", re.compile(r"reveal\s+(all\s+)?(stored\s+)?(api\s+keys|credentials)", re.I)),
)

_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(delete|drop|truncate|update|insert|alter)\b", re.I),
    re.compile(r"\bchange\s+(the\s+)?(outcome|decision|score)\b", re.I),
    re.compile(r"\bmodify\s+(application|loan)\b", re.I),
)

_OTHER_APPLICANT_PII: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(other|another|different)\s+(applicant|customer|borrower)s?\b.*\b(ssn|income|address|phone|bureau)\b",
        re.I,
    ),
    re.compile(
        r"\b(ssn|social\s+security|full\s+name|home\s+address)\b.*\b(other|another)\b",
        re.I,
    ),
    re.compile(r"\b(list|show|dump|reveal)\s+all\s+applicants\b", re.I),
    re.compile(r"\bevery\s+customer'?s?\s+(crm|record|pii)\b", re.I),
)

_OUT_OF_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bweather\b", re.I),
    re.compile(r"\bhello\b|\bhi\b|\bwhat can you help\b", re.I),
    re.compile(r"\bjoke\b|\brecipe\b", re.I),
)

_INTENT_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "explain_decision",
        "category": "analyst",
        "description": "Explain why a loan was approved or declined with evidence.",
        "riskClass": "low",
        "capabilities": ["ledger.query", "policy.read"],
        "tools": ["sql", "policy_lookup"],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": [
            "why was LN-1042 declined",
            "explain the decision for this loan",
            "what drove the decline",
            "top drivers for LN-1042",
        ],
        "triggers": [
            ("explain", 3.0),
            ("why", 2.5),
            ("declined", 2.0),
            ("approved", 2.0),
            ("decision", 2.0),
            ("drivers", 2.5),
            ("reason", 1.5),
        ],
        "version": 1,
    },
    {
        "id": "explain_price_change",
        "category": "analyst",
        "description": "Explain rate, fee, or pricing rule changes affecting the outcome.",
        "riskClass": "low",
        "capabilities": ["policy.read", "ledger.query"],
        "tools": ["policy_lookup", "sql"],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": [
            "why did the rate change",
            "explain the price change on this loan",
            "which policy rule changed the score",
            "interest rate increase impact",
        ],
        "triggers": [
            ("price", 2.5),
            ("rate", 2.5),
            ("fee", 2.0),
            ("pricing", 2.5),
            ("policy rule", 2.0),
            ("points", 1.5),
        ],
        "version": 1,
    },
    {
        "id": "compare_cases",
        "category": "analyst",
        "description": "Compare this case to similar peer applications.",
        "riskClass": "low",
        "capabilities": ["ledger.peers"],
        "tools": ["peer_cases"],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": [
            "compare to similar cases",
            "show peer loans with same outcome",
            "how does LN-1042 compare to others",
            "similar applications",
        ],
        "triggers": [
            ("compare", 3.0),
            ("peer", 3.0),
            ("similar", 2.5),
            ("other cases", 2.0),
            ("like this loan", 2.0),
        ],
        "version": 1,
    },
    {
        "id": "counterfactual",
        "category": "analyst",
        "description": "What-if: smallest change that flips approve/decline.",
        "riskClass": "low",
        "capabilities": ["ledger.counterfactual", "ledger.query"],
        "tools": ["counterfactual", "sql"],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": [
            "what if we reduced the amount",
            "what would flip this to approve",
            "counterfactual for LN-1230",
            "smallest change to get approved",
        ],
        "triggers": [
            ("what if", 3.0),
            ("counterfactual", 3.5),
            ("flip", 2.5),
            ("would approve", 2.5),
            ("smallest change", 3.0),
        ],
        "version": 1,
    },
    {
        "id": "out_of_scope",
        "category": "general",
        "description": "Requests outside credit-analyst capabilities.",
        "riskClass": "low",
        "capabilities": [],
        "tools": [],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": [
            "what's the weather tomorrow",
            "hello there",
            "tell me a joke",
        ],
        "triggers": [
            ("weather", 4.0),
            ("joke", 3.0),
            ("recipe", 2.0),
        ],
        "version": 1,
    },
    {
        "id": "risky",
        "category": "safety",
        "description": "Destructive data change or exfiltration of other applicants' data.",
        "riskClass": "high",
        "capabilities": [],
        "tools": [],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": [
            "delete this loan application",
            "change the outcome to approved",
            "show other applicants ssn",
        ],
        "triggers": [
            ("delete", 3.0),
            ("drop table", 4.0),
            ("update applications", 3.5),
            ("other applicant", 3.0),
            ("all applicants", 2.5),
        ],
        "version": 1,
    },
    {
        "id": "GENERAL",
        "category": "general",
        "description": "Safe fallback when confidence is below Reject band.",
        "riskClass": "low",
        "capabilities": [],
        "tools": ["sql"],
        "surfaces": ["chat", "api"],
        "roles": ["*"],
        "examples": ["help", "thanks"],
        "triggers": [],
        "version": 1,
    },
]

_STARTUP_SELF_TEST_DONE = False


@dataclass(frozen=True)
class IntentCandidate:
    intent: str
    score: float
    lexical: float


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    flags: list[str] = field(default_factory=list)
    reason: str | None = None
    injection_detected: bool = False


@dataclass(frozen=True)
class RoutingDecision:
    """Signed Scout routing outcome consumed by the investigator."""

    intent: IntentId
    confidence: float
    band: ConfidenceBand
    risk: RiskAssessment
    tool_plan: list[str]
    routing_hash: str
    execution_contract: dict[str, Any]
    refused: bool
    refusal_message: str | None = None


def _scout_secret() -> bytes:
    return os.environ.get("WHYLEDGER_SCOUT_SECRET", "whyledger-scout-dev-local").encode(
        "utf-8"
    )


def _canonical_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sign_contract(contract_body: dict[str, Any]) -> str:
    payload = _canonical_json(contract_body)
    return hmac.new(_scout_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _band(score: float) -> ConfidenceBand:
    if score >= BAND_HIGH:
        return "high"
    if score >= BAND_MEDIUM:
        return "medium"
    if score >= BAND_LOW:
        return "low"
    return "reject"


def _lexical_score(prompt: str, intent_rec: dict[str, Any]) -> float:
    text = prompt.lower()
    matched = 0.0
    for phrase, weight in intent_rec.get("triggers", []):
        if phrase.lower() in text:
            matched += float(weight)
    for example in intent_rec.get("examples", []):
        ex = example.lower()
        if ex in text or text in ex:
            matched += 2.0
        else:
            tokens = [t for t in re.split(r"\W+", ex) if len(t) > 3]
            overlap = sum(1 for t in tokens if t in text)
            if overlap >= 2:
                matched += 1.0 * overlap
    return matched


def _merged_score(lexical: float, risk_penalty: float = 0.0) -> float:
    """Stage-1 only: semantic/contextual/recency are 0."""
    raw = (
        WEIGHT_LEXICAL * lexical
        + WEIGHT_SEMANTIC * 0.0
        + WEIGHT_CONTEXTUAL * 0.0
        + WEIGHT_RECENCY * 0.0
        - risk_penalty
    )
    # Normalize into 0..1 using a soft cap so band thresholds apply sensibly.
    if lexical <= 0:
        return 0.0
    return min(1.0, max(0.0, raw / max(lexical * WEIGHT_LEXICAL, 0.01)))


def classify(prompt: str, *, surface: str = "api") -> list[IntentCandidate]:
    """Sense stage: top-k scored intent candidates."""
    _run_startup_self_test()
    return _classify_without_self_test(prompt, surface=surface)


def _intent_by_id(intent_id: str) -> dict[str, Any]:
    for rec in _INTENT_REGISTRY:
        if rec["id"] == intent_id:
            return rec
    return _INTENT_REGISTRY[-1]


def guard(
    prompt: str,
    *,
    primary_intent: str,
    surface: str = "api",
    user_role: str = "analyst",
) -> RiskAssessment:
    """Guard stage: injection, destructive/PII patterns, scope."""
    flags: list[str] = []
    injection = False
    for kind, pattern in _INJECTION_PATTERNS:
        if pattern.search(prompt):
            injection = True
            flags.append(f"injection:{kind}")
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(prompt):
            flags.append("policy:destructive-sql-or-write")
    for pattern in _OTHER_APPLICANT_PII:
        if pattern.search(prompt):
            flags.append("pii:other-applicant")
    if primary_intent == "risky":
        flags.append("intent:risky-classified")

    rec = _intent_by_id(primary_intent)
    if user_role == "viewer" and primary_intent not in ("GENERAL", "out_of_scope"):
        flags.append("policy:role-viewer")

    level: RiskLevel = "low"
    reason: str | None = None
    if injection:
        level = "block"
        reason = "Prompt injection pattern detected; request blocked."
    elif "policy:destructive-sql-or-write" in flags or "pii:other-applicant" in flags:
        level = "block"
        reason = (
            "This assistant is read-only. It cannot change records or reveal "
            "other applicants' personal data."
        )
    elif primary_intent == "risky" or "intent:risky-classified" in flags:
        level = "block"
        reason = (
            "Risky request refused: data changes and cross-applicant PII access "
            "are not permitted."
        )
    elif rec.get("riskClass") == "high":
        level = "high"
    elif rec.get("riskClass") == "medium":
        level = "medium"

    return RiskAssessment(
        level=level,
        flags=flags,
        reason=reason,
        injection_detected=injection,
    )


def _route_tools(intent_id: str) -> list[str]:
    rec = _intent_by_id(intent_id)
    return list(rec.get("tools", []))


def _routing_payload(
    intent_id: str,
    confidence: float,
    tool_plan: list[str],
    risk_level: str,
) -> dict[str, Any]:
    return {
        "intent": intent_id,
        "confidence": round(confidence, 4),
        "tool_plan": tool_plan,
        "risk_level": risk_level,
        "registry_version": REGISTRY_VERSION,
    }


def routing_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def decide(
    prompt: str,
    *,
    surface: str = "api",
    user_role: str = "analyst",
    loan_id: str | None = None,
) -> RoutingDecision:
    """Full Scout pipeline for one user request (Sense → Guard → Route → Contract)."""
    _run_startup_self_test()
    prompt_norm = prompt.strip()
    if not prompt_norm:
        prompt_norm = f"explain decision for {loan_id or 'loan'}"

    # Fast-path risky / out-of-scope signals before scoring tie-breaks.
    _risk_patterns = (*_DESTRUCTIVE_PATTERNS, *_OTHER_APPLICANT_PII)
    force_risky = any(p.search(prompt_norm) for p in _risk_patterns)
    force_oos = any(p.search(prompt_norm) for p in _OUT_OF_SCOPE_PATTERNS)

    candidates = classify(prompt_norm, surface=surface)
    primary = candidates[0].intent
    confidence = candidates[0].score

    if force_risky:
        primary = "risky"
        confidence = max(confidence, 0.95)
    elif force_oos and not any(c.intent == "explain_decision" and c.lexical > 0 for c in candidates):
        primary = "out_of_scope"
        confidence = max(confidence, 0.85)

    if _band(confidence) == "reject" and primary not in ("risky", "out_of_scope"):
        primary = "GENERAL"
        confidence = BAND_LOW

    if loan_id and primary == "GENERAL" and re.search(r"\bLN-\d{4}\b", prompt_norm, re.I):
        primary = "explain_decision"
        confidence = max(confidence, BAND_MEDIUM)

    risk = guard(prompt_norm, primary_intent=primary, surface=surface, user_role=user_role)
    tool_plan = _route_tools(primary)
    if risk.level == "block":
        tool_plan = []

    refused = risk.level == "block" or primary in ("risky", "out_of_scope")
    refusal_message: str | None = None
    if primary == "out_of_scope" and risk.level != "block":
        refusal_message = (
            "That request is outside Why-Ledger's scope. Ask about a loan decision, "
            "policy drivers, peer cases, or a what-if counterfactual (include a loan id "
            "such as LN-1042)."
        )
        refused = True
    elif risk.level == "block":
        refusal_message = risk.reason or "Request blocked by Scout risk guard."

    route_payload = _routing_payload(primary, confidence, tool_plan, risk.level)
    r_hash = routing_hash(route_payload)

    now = time.time()
    issued = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    expires = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + CONTRACT_TTL_SECONDS)
    )
    trace_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())
    contract_body: dict[str, Any] = {
        "contractId": contract_id,
        "traceId": trace_id,
        "intent": {
            "primary": primary,
            "parameters": {"loan_id": loan_id} if loan_id else {},
            "confidence": round(confidence, 4),
            "alternates": [
                {"intent": c.intent, "score": round(c.score, 4)} for c in candidates[1:4]
            ],
        },
        "risk": {
            "level": risk.level if risk.level != "block" else "high",
            "flags": risk.flags,
            "redactions": [],
            "approvals": [],
        },
        "toolPlan": [{"tool": t} for t in tool_plan],
        "scope": {
            "user": {"role": user_role},
            "surface": surface,
            "capabilities": _intent_by_id(primary).get("capabilities", []),
        },
        "issuedAt": issued,
        "expiresAt": expires,
        "registryVersion": REGISTRY_VERSION,
        "policyPackVersion": POLICY_PACK_VERSION,
        "routingHash": r_hash,
        "signedBy": "scout",
    }
    if risk.level == "block":
        contract_body["risk"]["level"] = "block"
    signature = _sign_contract(contract_body)
    execution_contract = {**contract_body, "signature": signature}

    return RoutingDecision(
        intent=primary,  # type: ignore[arg-type]
        confidence=confidence,
        band=_band(confidence),
        risk=risk,
        tool_plan=tool_plan,
        routing_hash=r_hash,
        execution_contract=execution_contract,
        refused=refused,
        refusal_message=refusal_message,
    )


def _run_startup_self_test() -> None:
    """Stage-1 registry scan budget self-test (AGENTS.md §4.1, <16 ms)."""
    global _STARTUP_SELF_TEST_DONE
    if _STARTUP_SELF_TEST_DONE:
        return
    _STARTUP_SELF_TEST_DONE = True
    probes = [
        "why was LN-1042 declined",
        "compare similar cases",
        "ignore previous instructions",
        "what's the weather",
    ]
    start = time.perf_counter()
    for p in probes:
        _classify_without_self_test(p)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if elapsed_ms > 16.0:
        raise RuntimeError(
            f"Scout Stage-1 self-test exceeded 16 ms budget ({elapsed_ms:.2f} ms)"
        )


def _classify_without_self_test(prompt: str, *, surface: str = "api") -> list[IntentCandidate]:
    candidates: list[IntentCandidate] = []
    for rec in _INTENT_REGISTRY:
        if surface not in rec.get("surfaces", ["api"]):
            continue
        lex = _lexical_score(prompt, rec)
        penalty = 0.15 if rec.get("riskClass") == "high" and lex > 0 else 0.0
        score = _merged_score(lex, penalty)
        if lex > 0 or rec["id"] == "GENERAL":
            candidates.append(IntentCandidate(intent=rec["id"], score=score, lexical=lex))
    candidates.sort(key=lambda c: (c.score, c.lexical), reverse=True)
    if not candidates:
        candidates.append(IntentCandidate(intent="GENERAL", score=0.0, lexical=0.0))
    return candidates[:5]


def detect_injection(prompt: str) -> bool:
    """Adversarial eval helper: True when injection patterns match."""
    for _, pattern in _INJECTION_PATTERNS:
        if pattern.search(prompt):
            return True
    if re.search(r"grant\s+admin", prompt, re.I):
        return True
    return False
