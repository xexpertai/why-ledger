"""Investigator agent: LLM-driven tool loop with evidence ledger and verifier.

Each loop iteration emits a :class:`TraceEvent` suitable for SSE (step, tool, sql,
row_count, note). Claims are drafted in the JSON shape used by ``fixtures/llm_ln1042.json``;
each claim is bound to logged SQL in the :class:`~whyledger.ledger.Ledger`, then
:class:`~whyledger.verifier.verify_claims` runs before :func:`~whyledger.card.build_card`.
Drivers on the card are ranked from scorecard points in the data, not from the LLM.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from whyledger.card import (
    Driver,
    Mode,
    WhyCard,
    aggregate_drivers_by_feature,
    build_card,
    enrich_why_card,
)
from whyledger.counterfactual import find_counterfactual
from whyledger.intent import RoutingDecision, decide as scout_decide
from whyledger.ledger import Claim, Ledger, rows_sha256
from whyledger.llm import LLMClient, extract_json_object, is_live_llm, llm_trace_meta
from whyledger.planned_evidence import evidence_table_json, gather_planned_evidence
from whyledger.prompts import (
    MAX_LIVE_LLM_CALLS,
    MAX_PLANNED_LLM_CALLS,
    load_investigator_system_prompt,
    load_planned_draft_system_prompt,
    planned_draft_field_glossary,
    planned_draft_loan_scope_rule,
    resolve_live_agent_mode,
)
from whyledger.scorecard import drivers_by_points, score
from whyledger.verifier import verify_claims
from whyledger.zetaris import QueryResult, ReadOnlySQLError, ZetarisClient
from whyledger.zetaris_dialect import adapt_sql, table_ref

MAX_AGENT_STEPS = 8

_LOAN_ID_RE = re.compile(r"\b(LN-\d{4})\b", re.IGNORECASE)

_SECTOR_RISK = {
    "hospitality": 2,
    "construction": 2,
    "retail": 1,
    "transport": 1,
}


@dataclass(frozen=True)
class TraceEvent:
    """One SSE-friendly trace row from the investigator loop."""

    step: int
    tool: str | None = None
    sql: str | None = None
    row_count: int | None = None
    note: str = ""
    intent: str | None = None
    routing_hash: str | None = None
    provider: str | None = None
    model: str | None = None


def _trace_with_llm(event: TraceEvent, llm: LLMClient) -> TraceEvent:
    provider, model = llm_trace_meta(llm)
    if provider is None:
        return event
    return TraceEvent(
        step=event.step,
        tool=event.tool,
        sql=event.sql,
        row_count=event.row_count,
        note=event.note,
        intent=event.intent,
        routing_hash=event.routing_hash,
        provider=provider,
        model=model,
    )


@dataclass
class InvestigationResult:
    """Full run output: trace stream materialised plus final WhyCard."""

    loan_id: str
    trace: list[TraceEvent] = field(default_factory=list)
    card: WhyCard | None = None
    routing: RoutingDecision | None = None
    refused: bool = False
    refusal_message: str | None = None


def _parse_llm_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM response JSON must be an object")
    return payload


MISSING_LOAN_MESSAGE = "Please name a loan, for example LN-1042"


def try_resolve_loan_id(loan_id: str | None, question: str | None) -> str | None:
    """Return LN-xxxx from explicit loan_id or question, or None if absent."""
    if loan_id and str(loan_id).strip():
        return str(loan_id).strip().upper()
    if question:
        match = _LOAN_ID_RE.search(question)
        if match:
            return match.group(1).upper()
    return None


def _resolve_loan_id(loan_id: str | None, question: str | None) -> str:
    resolved = try_resolve_loan_id(loan_id, question)
    if resolved:
        return resolved
    raise ValueError(
        "loan_id or question containing a loan id (e.g. LN-1042) is required"
    )


def _sector_risk(sector: str) -> float:
    return float(_SECTOR_RISK.get(sector.lower(), 0))


def _late_count_sql(prior_loan_id: str, client: ZetarisClient) -> str:
    lid = prior_loan_id.replace("'", "''")
    payments = table_ref("payments", client)
    return (
        "SELECT COUNT(*) AS late_count FROM ("
        f" SELECT late_days FROM {payments} WHERE loan_id = '{lid}'"
        " ORDER BY due_date DESC LIMIT 6"
        ") t WHERE CAST(late_days AS INT) > 15"
    )


def _policy_lookup_sql(
    rule_id: str | None, search: str | None, client: ZetarisClient
) -> str:
    if rule_id:
        rid = rule_id.replace("'", "''")
        rules = table_ref("policy_rules", client)
        return (
            "SELECT rule_id, feature, operator, threshold, points, text "
            f"FROM {rules} WHERE rule_id = '{rid}'"
        )
    term = (search or "").replace("'", "''")
    docs = table_ref("policy_docs", client)
    return (
        "SELECT doc_id, section, substr(text, 1, 200) AS excerpt "
        f"FROM {docs} WHERE text LIKE '%{term}%' OR section LIKE '%{term}%' "
        "LIMIT 5"
    )


def _peer_cases_sql(loan_id: str, client: ZetarisClient) -> str:
    lid = loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    return (
        "SELECT a2.loan_id, a2.outcome, a2.score, a2.amount "
        f"FROM {apps} a1 "
        f"JOIN {apps} a2 ON a1.outcome = a2.outcome AND a2.loan_id != a1.loan_id "
        f"WHERE a1.loan_id = '{lid}' "
        "ORDER BY ABS(CAST(a2.score AS DOUBLE) - CAST(a1.score AS DOUBLE)) "
        "LIMIT 5"
    )


def _load_policy_rules(client: ZetarisClient) -> list[dict[str, Any]]:
    rules = table_ref("policy_rules", client)
    result = client.run_sql(
        "SELECT rule_id, feature, operator, threshold, points, text "
        f"FROM {rules} ORDER BY rule_id"
    )
    rules: list[dict[str, Any]] = []
    for row in result.rows:
        rules.append(
            {
                "rule_id": str(row["rule_id"]),
                "feature": str(row["feature"]),
                "operator": str(row["operator"]),
                "threshold": float(row["threshold"]),
                "points": float(row["points"]),
                "text": str(row.get("text", "")),
            }
        )
    return rules


def _features_for_loan(client: ZetarisClient, loan_id: str) -> dict[str, float]:
    lid = loan_id.replace("'", "''")
    apps = table_ref("applications", client)
    app = client.run_sql(
        f"SELECT amount, term, sector, years_in_business, declared_income, "
        f"prior_loans, prior_loan_id, applicant_id FROM {apps} "
        f"WHERE loan_id = '{lid}'"
    )
    if not app.rows:
        raise ValueError(f"no application for loan_id={loan_id}")
    row = app.rows[0]
    applicant_id = str(row["applicant_id"]).replace("'", "''")
    bureau_t = table_ref("bureau", client)
    bureau = client.run_sql(
        f"SELECT utilisation, score, enquiries_6m, defaults FROM {bureau_t} "
        f"WHERE applicant_id = '{applicant_id}'"
    )
    if not bureau.rows:
        raise ValueError(f"no bureau row for applicant_id={row['applicant_id']}")
    b = bureau.rows[0]
    prior = str(row.get("prior_loan_id") or "")
    late = 0.0
    if prior:
        late_res = client.run_sql(_late_count_sql(prior, client))
        if late_res.rows:
            late = float(late_res.rows[0].get("late_count", 0))
    amount = float(row["amount"])
    income = float(row["declared_income"])
    sector = str(row.get("sector") or "")
    return {
        "amount": amount,
        "term": float(row["term"]),
        "years_in_business": float(row["years_in_business"]),
        "declared_income": income,
        "utilisation": float(b["utilisation"]),
        "bureau_score": float(b["score"]),
        "enquiries_6m": float(b["enquiries_6m"]),
        "defaults": float(b["defaults"]),
        "late_over_15_of_last_6": late,
        "amount_to_income": amount / income if income else 0.0,
        "prior_loans": float(row.get("prior_loans") or 0),
        "sector_risk": _sector_risk(sector),
    }


def _claim_id_for_feature(feature: str, claims: list[Claim]) -> str | None:
    hints: dict[str, tuple[str, ...]] = {
        "late_over_15_of_last_6": ("late_count", "late", "15 days"),
        "utilisation": ("utilisation", "util"),
        "defaults": ("defaults",),
        "amount_to_income": ("amount_to_income", "amount"),
        "bureau_score": ("bureau_score", "score"),
        "enquiries_6m": ("enquiries",),
        "declared_income": ("declared_income", "income"),
    }
    keys = hints.get(feature, (feature,))
    for claim in claims:
        blob = claim.text.lower()
        if any(k in blob for k in keys):
            return claim.id
        if any(k in claim.values for k in keys):
            return claim.id
    return None


def build_drivers_from_scorecard(
    *,
    client: ZetarisClient,
    loan_id: str,
    verified_claims: list[Claim],
) -> list[Driver]:
    """Rank drivers by absolute scorecard points (data-driven, not LLM)."""
    features = _features_for_loan(client, loan_id)
    rules = _load_policy_rules(client)
    result = score(features, rules)
    ranked = drivers_by_points(result)
    by_feature: dict[str, float] = {}
    claim_for: dict[str, str | None] = {}
    for hit in ranked:
        by_feature[hit.feature] = by_feature.get(hit.feature, 0.0) + abs(
            float(hit.points)
        )
        cid = _claim_id_for_feature(hit.feature, verified_claims)
        if cid and claim_for.get(hit.feature) is None:
            claim_for[hit.feature] = cid
    total_pts = sum(by_feature.values()) or 1.0
    ordered = sorted(by_feature.items(), key=lambda item: item[1], reverse=True)
    drivers: list[Driver] = []
    for rank, (feature, pts) in enumerate(ordered, start=1):
        weight = round(100.0 * pts / total_pts, 1)
        drivers.append(
            Driver(
                rank=rank,
                feature=feature,
                weight_pct=weight,
                claim_id=claim_for.get(feature),
            )
        )
    return aggregate_drivers_by_feature(drivers)


class Investigator:
    """Runs the plan → tool → draft_claims loop against Zetaris and the ledger."""

    def __init__(
        self,
        *,
        loan_id: str | None = None,
        question: str | None = None,
        client: ZetarisClient,
        llm: LLMClient,
        mode: Mode = "stub",
        max_steps: int = MAX_AGENT_STEPS,
    ) -> None:
        self.client = client
        self.llm = llm
        self.mode = mode
        self.max_steps = max_steps
        self.question = (question or "").strip()
        self.loan_id = try_resolve_loan_id(loan_id, question)
        self.ledger = Ledger()
        self._sql_to_query_id: dict[str, str] = {}
        self._routing: RoutingDecision | None = None
        self._live_llm = is_live_llm(llm)
        self._agent_mode = resolve_live_agent_mode(llm)
        self._evidence_id_to_query_id: dict[str, str] = {}
        if self._live_llm and self._agent_mode == "planned":
            self.max_steps = MAX_PLANNED_LLM_CALLS
            system_content = load_planned_draft_system_prompt()
        elif self._live_llm:
            self.max_steps = min(max_steps, MAX_LIVE_LLM_CALLS)
            system_content = load_investigator_system_prompt()
        else:
            system_content = (
                "You are a credit decision investigator. Reply with a single JSON object "
                "per turn: step plan|tool|draft_claims. Tools: sql, policy_lookup, "
                "peer_cases. draft_claims must list claims with id, text, values, sql."
            )
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": (
                    self.question
                    or (
                        f"Explain loan {self.loan_id}. Gather evidence then draft claims."
                        if self.loan_id
                        else "Explain a loan decision. Gather evidence then draft claims."
                    )
                ),
            },
        ]

    def _apply_scout_routing(self) -> TraceEvent:
        """Meterless Scout Intent: first step on every request."""
        if self.question:
            prompt = self.question
        elif self.loan_id:
            prompt = f"Explain the decision for loan {self.loan_id}"
        else:
            prompt = "Explain the loan decision"
        self._routing = scout_decide(
            prompt,
            surface="api",
            user_role="analyst",
            loan_id=self.loan_id,
        )
        route = self._routing
        tool_hint = (
            f" Scout route: intent={route.intent} tools={','.join(route.tool_plan)}."
        )
        self._messages[0] = {
            "role": "system",
            "content": self._messages[0]["content"] + tool_hint,
        }
        if route.tool_plan:
            self._messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Start with tool '{route.tool_plan[0]}' for this "
                        f"{route.intent} request on {self.loan_id}."
                    ),
                }
            )
        note = (
            f"intent={route.intent} confidence={route.confidence:.2f} "
            f"band={route.band} risk={route.risk.level} "
            f"tools={route.tool_plan}"
        )
        return TraceEvent(
            step=0,
            tool="scout_intent",
            note=note,
            intent=route.intent,
            routing_hash=route.routing_hash,
        )

    def _record_sql_evidence(self, result: QueryResult) -> str:
        key = result.sql.strip()
        existing = self._sql_to_query_id.get(key)
        if existing:
            return existing
        self.ledger.append_evidence(
            query_id=result.query_id,
            sql=result.sql,
            columns=result.columns,
            rows=result.rows,
            sha256=rows_sha256(result.rows),
        )
        self._sql_to_query_id[key] = result.query_id
        return result.query_id

    def _run_tool(self, payload: dict[str, Any]) -> tuple[str | None, int | None, str]:
        tool = str(payload.get("tool", "")).lower()
        sql: str | None = None
        row_count: int | None = None
        note = ""

        if tool == "sql":
            sql = adapt_sql(str(payload.get("sql", "")).strip(), self.client)
            if not sql:
                return None, None, "sql tool missing sql field"
            try:
                result = self.client.run_sql(sql)
            except ReadOnlySQLError as exc:
                return sql, None, f"read-only guard refused SQL: {exc}"
            except Exception as exc:
                return sql, None, f"sql execution refused: {exc}"
            self._record_sql_evidence(result)
            return sql, len(result.rows), "executed sql"

        if tool == "policy_lookup":
            rule_id = payload.get("rule_id")
            search = payload.get("search") or payload.get("query")
            sql = _policy_lookup_sql(
                str(rule_id) if rule_id else None,
                str(search) if search else None,
                self.client,
            )
            try:
                result = self.client.run_sql(sql)
            except ReadOnlySQLError as exc:
                return sql, None, f"read-only guard refused SQL: {exc}"
            except Exception as exc:
                return sql, None, f"sql execution refused: {exc}"
            self._record_sql_evidence(result)
            return sql, len(result.rows), "policy_lookup"

        if tool == "peer_cases":
            sql = _peer_cases_sql(self.loan_id, self.client)
            try:
                result = self.client.run_sql(sql)
            except Exception as exc:
                return sql, None, f"sql execution refused: {exc}"
            self._record_sql_evidence(result)
            return sql, len(result.rows), "peer_cases"

        return None, None, f"unknown tool: {tool}"

    def _claims_from_draft(self, payload: dict[str, Any]) -> list[Claim]:
        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list):
            return []
        out: list[Claim] = []
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id", ""))
            text = str(item.get("text", ""))
            values_raw = item.get("values") or {}
            if not isinstance(values_raw, dict):
                values_raw = {}
            values: dict[str, int | float | str] = {}
            for k, v in values_raw.items():
                if isinstance(v, bool):
                    values[str(k)] = str(v)
                elif isinstance(v, (int, float, str)):
                    values[str(k)] = v
                else:
                    values[str(k)] = str(v)
            claim_sql = adapt_sql(str(item.get("sql", "")).strip(), self.client)
            query_ids: list[str] = []
            raw_eids = item.get("evidence_ids")
            if isinstance(raw_eids, list):
                for eid in raw_eids:
                    qid = self._evidence_id_to_query_id.get(str(eid))
                    if qid and qid not in query_ids:
                        query_ids.append(qid)
            if claim_sql:
                key = claim_sql
                if key in self._sql_to_query_id:
                    query_ids.append(self._sql_to_query_id[key])
                else:
                    try:
                        result = self.client.run_sql(claim_sql)
                    except Exception:
                        pass
                    else:
                        qid = self._record_sql_evidence(result)
                        query_ids.append(qid)
            claim = Claim(id=cid, text=text, values=values, query_ids=query_ids)
            self.ledger.append_claim(claim)
            out.append(claim)
        return out

    def _decision_for_loan(self) -> str:
        lid = self.loan_id.replace("'", "''")
        apps = table_ref("applications", self.client)
        res = self.client.run_sql(
            f"SELECT outcome FROM {apps} WHERE loan_id = '{lid}'"
        )
        if res.rows:
            return str(res.rows[0].get("outcome", "unknown"))
        return "unknown"

    def iter_trace(self) -> Iterator[TraceEvent]:
        """Yield trace events from one loop pass (does not build the WhyCard)."""
        yield self._apply_scout_routing()
        if self._routing is not None and self._routing.refused:
            return
        if self._live_llm and self._agent_mode == "planned":
            trace, _ = self._loop_planned()
        else:
            trace, _ = self._loop()
        yield from trace

    def _loop(self) -> tuple[list[TraceEvent], list[Claim]]:
        trace: list[TraceEvent] = []
        drafted: list[Claim] = []
        step_ix = 0

        while step_ix < self.max_steps:
            step_ix += 1
            raw = self.llm.chat(self._messages, json_mode=True)
            self._messages.append({"role": "assistant", "content": raw})
            try:
                if self._live_llm:
                    payload = extract_json_object(raw)
                else:
                    payload = _parse_llm_payload(raw)
            except ValueError as exc:
                trace.append(
                    _trace_with_llm(
                        TraceEvent(
                            step=step_ix,
                            tool=None,
                            sql=None,
                            row_count=None,
                            note=f"LLM output refused: {exc}",
                        ),
                        self.llm,
                    )
                )
                break
            kind = str(payload.get("step", "")).lower()

            if kind == "plan":
                thought = str(payload.get("thought", ""))
                trace.append(
                    _trace_with_llm(
                        TraceEvent(
                            step=step_ix,
                            tool=None,
                            sql=None,
                            row_count=None,
                            note=thought or "plan",
                        ),
                        self.llm,
                    )
                )
                continue

            if kind == "tool":
                sql, row_count, note = self._run_tool(payload)
                trace.append(
                    TraceEvent(
                        step=step_ix,
                        tool=str(payload.get("tool", "")),
                        sql=sql,
                        row_count=row_count,
                        note=note,
                    )
                )
                continue

            if kind == "draft_claims":
                drafted = self._claims_from_draft(payload)
                trace.append(
                    _trace_with_llm(
                        TraceEvent(
                            step=step_ix,
                            tool="draft_claims",
                            sql=None,
                            row_count=len(drafted),
                            note="drafted claims",
                        ),
                        self.llm,
                    )
                )
                break

            trace.append(
                _trace_with_llm(
                    TraceEvent(
                        step=step_ix,
                        tool=None,
                        sql=None,
                        row_count=None,
                        note=f"unrecognized step: {kind}",
                    ),
                    self.llm,
                )
            )

        return trace, drafted

    def _loop_planned(self) -> tuple[list[TraceEvent], list[Claim]]:
        trace: list[TraceEvent] = []
        step_ix = 0
        try:
            items = gather_planned_evidence(self.client, self.loan_id)
        except Exception as exc:
            trace.append(
                TraceEvent(
                    step=1,
                    tool="planned_evidence",
                    sql=None,
                    row_count=None,
                    note=f"evidence plan failed: {exc}",
                )
            )
            return trace, []

        for item in items:
            self._evidence_id_to_query_id[item.evidence_id] = item.query_id
            self.ledger.append_evidence(
                query_id=item.query_id,
                sql=item.sql,
                columns=item.columns,
                rows=item.rows,
                sha256=rows_sha256(item.rows),
            )
            self._sql_to_query_id[item.sql.strip()] = item.query_id
            step_ix += 1
            trace.append(
                TraceEvent(
                    step=step_ix,
                    tool="planned_evidence",
                    sql=item.sql,
                    row_count=len(item.rows),
                    note=f"{item.evidence_id} ({item.label})",
                )
            )

        table = evidence_table_json(items)
        draft_messages: list[dict[str, str]] = [
            {"role": "system", "content": self._messages[0]["content"]},
            {
                "role": "user",
                "content": (
                    f"Loan {self.loan_id}. Evidence table (SYNTHETIC demo): {table}\n"
                    f"{planned_draft_field_glossary()}\n"
                    f"{planned_draft_loan_scope_rule(self.loan_id)}\n"
                    "Draft factual claims citing evidence_ids and sql."
                ),
            },
        ]
        drafted: list[Claim] = []
        attempts = 0
        while attempts < MAX_PLANNED_LLM_CALLS:
            attempts += 1
            raw = self.llm.chat(draft_messages, json_mode=True)
            draft_messages.append({"role": "assistant", "content": raw})
            try:
                payload = extract_json_object(raw)
            except ValueError as exc:
                trace.append(
                    _trace_with_llm(
                        TraceEvent(
                            step=step_ix + attempts,
                            tool="draft_claims",
                            sql=None,
                            row_count=None,
                            note=f"LLM output refused: {exc}",
                        ),
                        self.llm,
                    )
                )
                if attempts >= MAX_PLANNED_LLM_CALLS:
                    break
                draft_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply was not valid JSON. "
                            'Reply with only {"step":"draft_claims","claims":[...]}'
                        ),
                    }
                )
                continue
            kind = str(payload.get("step", "")).lower()
            if kind != "draft_claims":
                trace.append(
                    _trace_with_llm(
                        TraceEvent(
                            step=step_ix + attempts,
                            tool="draft_claims",
                            sql=None,
                            row_count=None,
                            note=f"unexpected step: {kind}",
                        ),
                        self.llm,
                    )
                )
                if attempts >= MAX_PLANNED_LLM_CALLS:
                    break
                draft_messages.append(
                    {
                        "role": "user",
                        "content": (
                            'Reply with step "draft_claims" and a claims array only.'
                        ),
                    }
                )
                continue
            drafted = self._claims_from_draft(payload)
            trace.append(
                _trace_with_llm(
                    TraceEvent(
                        step=step_ix + attempts,
                        tool="draft_claims",
                        sql=None,
                        row_count=len(drafted),
                        note="drafted claims",
                    ),
                    self.llm,
                )
            )
            break

        return trace, drafted

    def run(self) -> InvestigationResult:
        """Execute the full loop, verify claims, and build the WhyCard."""
        trace: list[TraceEvent] = [self._apply_scout_routing()]
        route = self._routing
        if route is not None and route.refused:
            return InvestigationResult(
                loan_id=self.loan_id or "",
                trace=trace,
                card=None,
                routing=route,
                refused=True,
                refusal_message=route.refusal_message,
            )
        if not self.loan_id:
            return InvestigationResult(
                loan_id="",
                trace=trace,
                card=None,
                routing=route,
                refused=True,
                refusal_message=MISSING_LOAN_MESSAGE,
            )
        if self._live_llm and self._agent_mode == "planned":
            loop_trace, drafted = self._loop_planned()
        else:
            loop_trace, drafted = self._loop()
        trace.extend(loop_trace)
        results = verify_claims(
            drafted, self.ledger, self.client, subject_loan_id=self.loan_id
        )
        verified_only = [
            c
            for c in drafted
            if any(
                r.claim_id == c.id and r.status == "VERIFIED"
                for r in results
            )
        ]
        drivers = build_drivers_from_scorecard(
            client=self.client,
            loan_id=self.loan_id,
            verified_claims=verified_only,
        )
        decision = self._decision_for_loan()
        counterfactual = None
        if decision == "decline":
            cf_search = find_counterfactual(self.client, self.loan_id)
            if cf_search is not None:
                for qr in cf_search.queries:
                    self._record_sql_evidence(qr)
                counterfactual = cf_search.counterfactual
        card = build_card(
            decision=decision,
            mode=self.mode,
            claims=drafted,
            results=results,
            ledger=self.ledger,
            drivers=drivers,
            counterfactual=counterfactual,
            loan_id=self.loan_id,
            model=self.llm.label,
        )
        card = enrich_why_card(
            card,
            client=self.client,
            loan_id=self.loan_id,
            ledger=self.ledger,
            counterfactual=counterfactual,
        )
        return InvestigationResult(
            loan_id=self.loan_id,
            trace=trace,
            card=card,
            routing=route,
        )
