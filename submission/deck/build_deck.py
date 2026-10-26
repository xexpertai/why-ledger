"""Build submission/Why-Ledger-deck.pdf (16:9, 11 slides) from real UI screenshots in docs/screenshots/.
HTML slides rendered to PDF with headless Chromium (Playwright).
Run: uv run --no-project --with playwright python submission/deck/build_deck.py"""
from pathlib import Path
import base64, html
from playwright.sync_api import sync_playwright

R = Path(__file__).resolve().parents[2]; SS = R / "docs/screenshots"; OUT = R / "submission/Why-Ledger-deck.pdf"

def img(name, cap=""):
    b = base64.b64encode((SS / name).read_bytes()).decode()
    return f'<figure><img src="data:image/png;base64,{b}"><figcaption>{html.escape(cap)}</figcaption></figure>'

def ul(items): return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"

slides = []
def slide(title, body, sub=""):
    slides.append(f'<section class="s"><h1>{title}</h1>{f"<h2>{sub}</h2>" if sub else ""}<div class="body">{body}</div>'
                  f'<footer><span>SYNTHETIC data. Harbor Credit is a fictional lender.</span><span>Why-Ledger &middot; {len(slides)+1}</span></footer></section>')

slides.append('''<section class="s title"><h0>Why-Ledger</h0><p class="tag">An agent that checks every claim against the data</p>
<p>Track 2: The Agent That Can Explain Why &nbsp;|&nbsp; Open Agent Hackathon 2026</p><p class="by">Harsha Shinde (solo entrant)</p>
<p class="mut">AI-assisted: production code written by Cursor (cursor-agent), directed and reviewed with Claude Code;<br>Narration: AI text-to-speech voice (Kokoro).</p>
<footer><span>SYNTHETIC data. Harbor Credit is a fictional lender.</span><span>Why-Ledger &middot; 1</span></footer></section>''')

slide("Problem and target users", '''<div class="two"><div class="card"><h3>The problem</h3>''' + ul([
    "A loan decision depends on applications, payments, bureau data and policy rules held in different systems.",
    "Regulators and applicants need the reasons.",
    "An LLM explanation can state a value or a reason that the data does not support.",
    "Even the reason code stored on the decision record can be wrong."]) + '''</div><div class="card"><h3>Who it is for</h3>''' + ul([
    "Credit analysts and reviewers",
    "Model-risk and compliance teams",
    "Applicants, through an illustrative reasons letter",
    "No time-saved or accuracy figures are claimed; all data is synthetic."]) + "</div></div>")

slide("The solution: a Why-Card in which every claim is checked",
      '<div class="two wide">' + img("recorded-live-ln-1042.png", "LN-1042 (recorded live run, SYNTHETIC): drivers, counterfactual, 6 drafted / 6 verified / 0 rejected") +
      '<div>' + ul(["Drivers ranked from scorecard points in the data, not by the model",
                    "Only VERIFIED claims reach the card; unsupported drafts go to a rejected panel",
                    "Counterfactual for declines: the smallest change that flips the decision",
                    "Evidence drawer: SQL, rows and SHA-256 for every claim",
                    "Illustrative applicant letter of ranked principal reasons (not legal advice)"]) + "</div></div>")

slide("It caught the lender's own wrong reason",
      '<div class="two wide">' + img("recorded-live-ln-1301.png", "LN-1301: red flag, Recorded reason not supported by data (SYNTHETIC)") +
      '<div>' + ul(["The decision record says INCOME_BELOW_THRESHOLD. Declared income is 110,000, above the 40,000 policy threshold, so rule R01 is not triggered.",
                    "The check re-runs SQL on Zetaris and shows record and data side by side. It states what the record says; it does not assign blame.",
                    "Data-supported drivers: bureau score 56.1 %, credit enquiries 35.7 %. Counterfactual: bureau score 580 flips the decline to approve.",
                    "Planted mismatches flagged: 1 of 1. False flags on the other 3 loans: 0."]) + "</div></div>",
      "Loan LN-1301")

steps = [("Question", "e.g. why was LN-1301 declined?"), ("Scout Intent", "Meterless: sense, guard, route"), ("Evidence", "read-only SQL on Zetaris"),
         ("Ledger", "rows + SHA-256, append-only"), ("Draft", "Nemotron via Nightshift writes claims that cite queries"),
         ("Verifier", "no LLM: re-runs the SQL, compares hash, value, column and loan scope"), ("Why-Card", "verified claims, drivers, counterfactual, reason check, letter")]
flow = '<div class="flow">' + '<span class="arr">&rsaquo;</span>'.join(f'<div class="step{" ok" if n=="Verifier" else ""}"><b>{a}</b><small>{b}</small></div>' for n, (a, b) in enumerate(steps) for n in [a]) + '</div>'
slide("Agent workflow and architecture", flow + ul([
    "Zetaris is the only data path; the model sees data only through logged queries.",
    "Planned mode: code chooses the evidence queries and one Nemotron call drafts the claims. Rejected claims are shown, never hidden.",
    "Recorded-reason check (deterministic): compares the reason code on the record with the policy rule and the data."]))

rows = [("Zetaris", "Sole data path. Live JDBC to Zetaris 2.4.3.1 (local Docker) over SYNTHETIC tables; agent queries and verifier re-runs are read-only SQL. It is also the verifier's oracle for what the model cited."),
        ("Meterless Scout Intent", "Every question is sensed, guarded and routed before any model runs; injected prompts are refused (tested on a port of the upstream adversarial set)."),
        ("Meterless Nightshift", "Primary LLM provider: an OpenAI-compatible runtime on the Windows host, reached from WSL. All four recorded runs show provider meterless-nightshift."),
        ("NVIDIA Nemotron", "nvidia/nemotron-3.5-lightning-30b-a3b via Nightshift drafts the claims (1 call per loan)."),
        ("Cursor", "All production code (src/, scripts/, UI, tests) written by cursor-agent; every task logged in docs/CURSOR-LOG.md."),
        ("What is unique", "The model's own cited SQL is re-executed on Zetaris and each stated value is tied to its named column; the record's reason code is checked against the data too.")]
slide("Technical stack and sponsor technology", '<table class="t">' + "".join(f"<tr><th>{a}</th><td>{b}</td></tr>" for a, b in rows) + "</table>")

slide("Product visuals: evidence-backed applicant letter",
      '<div class="two">' + img("applicant-letter-ln-1230.png", "LN-1230: principal reasons in rank order, #1 amount-to-income 52.1 %") +
      img("applicant-letter-ln-1301.png", "LN-1301: notes the unsupported recorded reason, lists data-supported reasons") + "</div>"
      '<p class="mut">Built from logged, hash-checked query evidence ([E1], [E2] chips point to query results). Illustrative, not legal advice.</p>')

ev = [("LN-1042", 6, 6, 0, "yes", "10.2 s"), ("LN-1107", 5, 5, 0, "yes", "6.7 s"), ("LN-1230", 6, 6, 0, "yes", "7.8 s"), ("LN-1301", 5, 5, 0, "yes", "6.8 s")]
tbl = '<table class="g"><tr><th>Loan</th><th>Drafted</th><th>Verified</th><th>Rejected</th><th>Top driver = truth</th><th>Latency</th></tr>' + \
      "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in ev) + '<tr class="tot"><td>Total</td><td>22</td><td>22</td><td>0</td><td>4/4</td><td></td></tr></table>'
slide("Evaluation: recorded Nightshift run, 25 Oct 2026", tbl + ul([
    "Recorded-reason check (from the replay bundles): 1 of 1 planted mismatch flagged (LN-1301); 0 false flags on the other 3.",
    "Scout intent routing 12/12 on 12 author-written cases (eval/results-live-zetaris.md). One run per loan: no variance claim.",
    "An earlier recording the same day took 67 s to 534 s on the shared free-tier model; this one 6.7 s to 10.2 s."]),
      "eval/results-live-nightshift.md and replay bundles (reason check): four planted loans, one run each, SYNTHETIC")

slide("Verifier stress test and honest limits",
      '<div class="two wide">' + img("verifier-stress.png", "PLANTED test claims, not model output") + '<div>' + ul([
          "14 planted cases on live Zetaris: 4 VERIFIED, 10 UNSUPPORTED",
          "Verdict matched 14/14; intended failure reason matched 14/14 (reported separately)",
          "Honest failure: an earlier LN-1230 attempt crashed on a non-existent column; the verifier now rejects such claims",
          "Limits: synthetic data, 4 loans, one Nemotron model works on this account, one run per loan, not compliance advice"]) + "</div></div>")

slide("Future enhancements", ul([
    "Model-proposed SQL loop mode with a hardened verifier (the one live loop run was weak: eval/live-nemotron-smoke.md)",
    "Federation across more Zetaris sources inside one verified query",
    "Richer policy engine and more reason-code checks beyond income",
    "Meterless H-MEM precedent memory: reuse verified explanations across similar cases",
    "Multiple runs per loan for latency and agreement ranges",
    "Real-data governance review before any production use"]))

slide("Team and disclosure", ul([
    "Entrant: Harsha Shinde (solo).",
    "Production code written by Cursor (cursor-agent; composer-2.5, Cursor Auto for the first tasks); log in docs/CURSOR-LOG.md.",
    "Directed and reviewed with Claude Code (Anthropic): design, prompts, docs, test review, video and deck assembly.",
    "Narration: AI text-to-speech voice (Kokoro).",
    "All data, Harbor Credit, applicants and loan IDs are fictional and labelled SYNTHETIC.",
    "Repo started 22 Oct 2026; no pre-existing components. Numbers come from recorded runs in eval/.",
    "Reason-check and letter fields in the recorded bundles were regenerated offline, deterministically, from the stored evidence rows (no new model call).",
    "Repo: https://github.com/xexpertai/why-ledger"]))

CSS = """
@page{size:13.333in 7.5in;margin:0} *{box-sizing:border-box} body{margin:0;background:#0f1624;color:#e8eef8;font-family:'Noto Sans','DejaVu Sans',sans-serif}
.s{width:13.333in;height:7.5in;padding:.45in .6in .6in;position:relative;page-break-after:always;overflow:hidden;background:#0f1624}
h1{font-size:34pt;margin:0 0 .1in} h2{font-size:17pt;color:#4da3ff;font-weight:400;margin:0 0 .1in} h3{color:#4da3ff;font-size:20pt;margin:0 0 .1in}
.body{font-size:17pt;line-height:1.35} ul{margin:.05in 0 0;padding-left:.28in} li{margin:.08in 0}
footer{position:absolute;left:0;right:0;bottom:0;height:.38in;background:#281c08;color:#fabe5a;font-weight:700;font-size:12pt;display:flex;justify-content:space-between;align-items:center;padding:0 .4in}
footer span:last-child{color:#96a6be;font-weight:400}
.two{display:grid;grid-template-columns:1fr 1fr;gap:.35in;align-items:start}.two.wide{grid-template-columns:7.4in 1fr}
.card{background:#182440;border-radius:12px;padding:.25in .3in;min-height:4.8in}
figure{margin:0}img{width:100%;border:1.5px solid #46526a;border-radius:6px;display:block} figcaption{font-size:11pt;color:#96a6be;margin-top:.05in}
.mut{color:#96a6be;font-size:14pt}
.title{display:flex;flex-direction:column;justify-content:center} h0{font-size:70pt;font-weight:700;display:block}.tag{font-size:30pt;color:#4da3ff;margin:.1in 0}.by{font-size:24pt;font-weight:700}.title p{margin:.1in 0;font-size:18pt}
.flow{display:flex;align-items:stretch;gap:0;margin:.15in 0 .3in}.step{background:#182440;border:2px solid #4da3ff;border-radius:10px;padding:.12in .1in;width:1.6in;text-align:center;font-size:12pt}
.step b{display:block;color:#4da3ff;font-size:15pt;margin-bottom:.06in}.step.ok{border-color:#4ade80}.step.ok b{color:#4ade80}.step small{font-size:11.5pt}.arr{align-self:center;font-size:26pt;color:#96a6be;width:.22in;text-align:center}
.t{border-collapse:separate;border-spacing:0 .07in;width:100%}.t th{width:2.5in;text-align:left;color:#4da3ff;font-size:15pt;padding:.08in .15in;background:#182440;border-radius:8px 0 0 8px;vertical-align:middle}.t td{font-size:13pt;padding:.08in .15in;background:#182440;border-radius:0 8px 8px 0;line-height:1.25}
.g{width:100%;border-collapse:collapse;margin:.1in 0 .15in}.g th{background:#104e3c;padding:.1in;font-size:15pt}.g td{background:#182440;text-align:center;padding:.1in;font-size:17pt;border-top:1px solid #0f1624}.g .tot td{font-weight:700;color:#4ade80}
"""
doc = f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(slides)}</body></html>"
(R / "submission/deck/deck.html").write_text(doc, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); pg.goto((R / "submission/deck/deck.html").as_uri()); pg.wait_for_timeout(800)
    pg.pdf(path=str(OUT), width="13.333in", height="7.5in", print_background=True, prefer_css_page_size=True); b.close()
print(OUT, len(slides), "slides")
