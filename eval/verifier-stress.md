# Verifier stress test (PLANTED test claims, not model output)

**Recorded:** 2026-10-25T17:06:34Z
**Zetaris backend:** ZETARIS-JDBC

| Loan | Claim | Planted truth | Verdict | Reason |
| --- | --- | --- | --- | --- |
| LN-1042 | late_count is 3 (SQL filters prior loan without prior-loan wording) | wrong loan scope: SQL uses LN-0900 but claim omits prior-loan label | UNSUPPORTED | evidence is for a different loan (LN-0900) |
| LN-1042 | 3 late payments on prior loan LN-0900 (SYNTHETIC planted true) | true: prior-loan conduct with explicit prior-loan text | VERIFIED | hash matched and asserted values match named columns in re-run rows |
| LN-1042 | declared income is 20000 (SYNTHETIC planted false) | false: declared_income is 72000 | UNSUPPORTED | asserted value does not match column in evidence rows: declared_income |
| LN-1042 | utilisation value under late_count key (wrong column) | wrong column: 91 is utilisation, not late_count | UNSUPPORTED | asserted value does not match column in evidence rows: late_count |
| LN-1042 | non-existent column name | non-existent column phantom_metric | UNSUPPORTED | asserted column not in query result: phantom_metric |
| LN-1107 | outcome approve (SYNTHETIC planted true) | true: outcome column is approve | VERIFIED | hash matched and asserted values match named columns in re-run rows |
| LN-1107 | defaults is 0 (SYNTHETIC planted false) | false: defaults is 2 | UNSUPPORTED | asserted value does not match column in evidence rows: defaults |
| LN-1107 | defaults count under outcome key | wrong column: 2 is defaults, not outcome | UNSUPPORTED | asserted value does not match column in evidence rows: outcome |
| LN-1230 | scorecard points 137 (SYNTHETIC planted true) | true: scorecard_points alias column is 137 | VERIFIED | hash matched and asserted values match named columns in re-run rows |
| LN-1230 | scorecard points 200 (SYNTHETIC planted false) | false: scorecard_points is 137 | UNSUPPORTED | asserted value does not match column in evidence rows: scorecard_points |
| LN-1230 | SQL uses display alias bureau_score without AS (planted bad SQL) | bad SQL: bureau_score is not a physical column | UNSUPPORTED | claim SQL failed on Zetaris: ZetarisJDBCError: bureau_score |
| LN-1301 | enquiries_6m is 7 (SYNTHETIC planted true) | true: enquiries_6m column is 7 | VERIFIED | hash matched and asserted values match named columns in re-run rows |
| LN-1301 | bureau score under enquiries_6m key | wrong column: 560 is bureau score, not enquiries | UNSUPPORTED | asserted value does not match column in evidence rows: enquiries_6m |
| LN-1301 | fictitious column credit_tier | non-existent column credit_tier | UNSUPPORTED | asserted column not in query result: credit_tier |

## Totals

- Cases: 14
- VERIFIED: 4
- UNSUPPORTED: 10
- Verdict matched: 14/14
- Reason matched: 14/14

_PLANTED test claims, not model output_
