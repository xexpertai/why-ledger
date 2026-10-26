# Why-Ledger: the chosen idea (Track 2, "The Agent That Can Explain Why")

One line: an agent that answers "why was this credit decision made?" for a small-business lender, with every claim tied to a SQL query run through Zetaris, and a verifier that re-runs those queries before the answer is shown.

## The five answers
1. **User:** a loan-operations analyst or compliance officer at a small lender (fictional "Harbor Credit", synthetic data) who must explain a decline or a price to the applicant, an auditor or a regulator.
2. **Problem:** the reasons sit in four places (application DB, payment history files, credit-bureau API, policy documents). Today an analyst spends hours stitching them, and the explanation written afterwards is often a plausible story, not a traceable one. Adverse-action and audit rules need the real reason.
3. **Input:** a decision ID (for example LN-1042) or a free-text question ("why was Meera's repeat loan priced 4 points higher than her first?").
4. **Output:** a "Why-Card": the decision, ranked drivers with weights, an evidence table (each claim, the SQL that proves it, the returned rows, a row hash), a counterfactual ("approved if X changed"), a plain-language applicant letter, and a verification stamp (all cited queries re-run and matched).
5. **Why an agent:** the question is open ended. The agent must plan which sources to query, run follow-ups when a first result is surprising, compare against policy rules and peer cases, reject its own unsupported claims, and loop until each claim has evidence. A fixed dashboard cannot do that.

## Why it can win Track 2
- Impact (30): fair-lending explainability is a real, regulated pain (adverse-action notices, audit trails). Clear target user.
- Technical (20): claim-level evidence binding plus an independent verifier (re-execution and hash check), not a prompt that "cites sources".
- Innovation (15): "No evidence, no claim" ledger; counterfactual search done by SQL, not by model guesswork; unfaithful-explanation detector (flags when the model's story contradicts the data).
- Demo (15): one decision, three visible agent steps, one planted trap where the first explanation is wrong and the verifier catches it.
- Sponsor (10): Zetaris is the only data path (federated SQL over CSV, Parquet, REST). Nemotron is the only reasoning model. Meterless is the route to Nemotron (and optionally its H-MEM/Markovian engines for case memory, see ARCHITECTURE).

## Honest limits
- All data is synthetic and labelled. No real lender, no real people, no legal advice.
- Model scores are from a transparent rule scorecard we define, so the "why" is exact; the agent explains and verifies it, and also explains a second, black-box-style score by counterfactual SQL probing.
