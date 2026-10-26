# Synthetic demo dataset plan ("Harbor Credit", all fictional, labelled SYNTHETIC in every table and in the UI)

Generator: `data/generate.py`, seeded (seed=2026) so it is reproducible. No real names: persona names are generated and flagged `is_synthetic=true`.

| Table | Format | Rows | Key columns |
|---|---|---|---|
| applications | CSV | 2,000 | loan_id, applicant_id, amount, term, sector, years_in_business, declared_income, outcome, rate, decided_at |
| payments | Parquet | ~30,000 | applicant_id, loan_id, due_date, paid_date, amount, late_days |
| bureau | REST (local mock service on :8077) | 1,400 | applicant_id, score, utilisation, enquiries_6m, defaults |
| policy_rules | CSV | ~25 | rule_id, feature, operator, threshold, points, text |
| policy_docs | markdown chunks | ~12 | doc_id, section, text (lender credit policy, written by us) |

## Planted scenarios (the demo and tests depend on these)
- **LN-1042 (hero):** decline. True drivers: late payments on a previous loan (3 of last 6 over 15 days) and utilisation 91%. A tempting wrong story: "low declared income". The model's first draft may say income; the verifier must reject it (income is above the policy threshold).
- **LN-1107:** repeat borrower, priced +4 points vs first loan. Driver: new 2 defaults in bureau API only (fragmentation case).
- **LN-1230:** borderline decline; counterfactual shows one change (reduce amount by 15%) flips to approve (debt-service / amount-to-income).
- **LN-1301:** a decision where the recorded reason code contradicts the data (policy exception). Agent should flag "recorded reason not supported".
- 6 further random cases for batch tests.

Rules: scorecard weights are fixed in `policy_rules`; ground-truth drivers per planted loan are stored in `data/truth.json` (tests only, never given to the agent).
