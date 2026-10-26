"""Load investigator prompts documented in ``docs/PROMPTS.md``."""

from __future__ import annotations

import os
import re
from pathlib import Path

from whyledger.llm import LLMClient, is_live_llm

MAX_LIVE_LLM_CALLS = 4
MAX_PLANNED_LLM_CALLS = 2

# Shown with planned evidence so the model does not confuse scorecard totals with bureau scores.
PLANNED_FIELD_GLOSSARY = """\
Real Zetaris columns (WHY_LEDGER_SYN; use these in claim SQL — not display aliases):
- applications: loan_id, applicant_id, outcome, reason_code, amount, declared_income, score (internal scorecard total), prior_loan_id, …
- bureau: applicant_id, score, utilisation, enquiries_6m, defaults, lender, note, is_synthetic
- payments: loan_id, due_date, paid_date, late_days, amount, …
- policy_rules: rule_id, title, text, …
- policy_docs: doc_id, section, text, …

Evidence table display labels (SELECT aliases only — not real column names):
- scorecard_points — alias for applications.score (or peer score); say "scorecard points" in text.
- bureau_score — alias for bureau.score; say "bureau score" in text.

In claim SQL use score (bureau) or applications.score with AS if needed; never SELECT bureau_score or scorecard_points unless you alias them yourself. Never call scorecard_points a "credit score".

Each key in claim `values` must match a column name returned by that claim's SQL (including SELECT aliases). The verifier checks the value against that column only, not other cells.

Loan scope: the case subject loan_id must appear in claim SQL filters unless the claim explicitly describes a prior loan (say "prior loan LN-xxxx" in text) or peer cases (ev_peer_cases evidence).\
"""


def planned_draft_loan_scope_rule(loan_id: str) -> str:
    """Remind the draft model to keep SQL scoped to the subject loan."""
    lid = loan_id.strip().upper()
    return (
        f"Case subject loan is {lid}. Use loan_id = '{lid}' in claim SQL for this "
        f"application unless the claim text explicitly names a prior loan (include "
        f"'prior loan LN-xxxx') or cites peer cases from ev_peer_cases."
    )

_PROMPTS_MD = Path(__file__).resolve().parents[2] / "docs" / "PROMPTS.md"
_FENCE_RE = re.compile(
    r"## Investigator system prompt\s+```(?:text)?\s*(.*?)```",
    re.DOTALL,
)
_PLANNED_FENCE_RE = re.compile(
    r"## Planned evidence draft prompt\s+```(?:text)?\s*(.*?)```",
    re.DOTALL,
)


def load_investigator_system_prompt() -> str:
    """Return the live Nemotron system prompt from ``docs/PROMPTS.md``."""
    if not _PROMPTS_MD.is_file():
        raise FileNotFoundError(f"missing prompt doc: {_PROMPTS_MD}")
    text = _PROMPTS_MD.read_text(encoding="utf-8")
    match = _FENCE_RE.search(text)
    if not match:
        raise ValueError("PROMPTS.md missing Investigator system prompt fenced block")
    return match.group(1).strip()


def planned_draft_field_glossary() -> str:
    """Short column semantics for planned-mode draft claims."""
    return PLANNED_FIELD_GLOSSARY.strip()


def load_planned_draft_system_prompt() -> str:
    """Return the planned-mode draft-claims system prompt from ``docs/PROMPTS.md``."""
    if not _PROMPTS_MD.is_file():
        raise FileNotFoundError(f"missing prompt doc: {_PROMPTS_MD}")
    text = _PROMPTS_MD.read_text(encoding="utf-8")
    match = _PLANNED_FENCE_RE.search(text)
    if not match:
        raise ValueError("PROMPTS.md missing Planned evidence draft prompt fenced block")
    return match.group(1).strip()


def resolve_live_agent_mode(llm: LLMClient) -> str:
    """``planned`` (default on live LLM) or ``loop`` (tool-step investigator)."""
    explicit = os.environ.get("LIVE_AGENT_MODE", "").strip().lower()
    if explicit in ("planned", "loop"):
        return explicit
    if is_live_llm(llm):
        return "planned"
    return "loop"
