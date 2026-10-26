"""Capture UI stills at 1920x1080 for the Why-Ledger demo (replay of recorded runs only; no secrets on these pages)."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).parent / "shots"
URL = "http://127.0.0.1:8090"
META = {}
VW, VH, DSF = 1440, 710, 4/3  # zoomed capture: 1440x710 CSS px at 1.333x = 1920x947 px (UI text 1.33x larger than a 1080p capture)

def shot(page, name, target=None, off=90, hl=None, y=None):
    """Scroll `target` css selector near top (or absolute y), screenshot, store highlight bbox (viewport coords)."""
    if target:
        page.evaluate("(a)=>{const e=document.querySelector(a[0]);window.scrollTo(0,e.getBoundingClientRect().top+window.scrollY-a[1]);}", [target, off])
    elif y is not None:
        page.evaluate(f"window.scrollTo(0,{y})")
    page.wait_for_timeout(900)
    boxes = []
    for h in ([hl] if isinstance(hl, str) else (hl or [])):
        bb = page.locator(h).first.bounding_box()
        if bb: boxes.append([round(bb["x"]*DSF), round(bb["y"]*DSF), round(bb["width"]*DSF), round(bb["height"]*DSF)])
    page.screenshot(path=str(OUT / f"{name}.png"))
    META[name] = boxes
    print("shot", name, boxes, flush=True)

def run_loan(page, loan):
    page.evaluate("document.getElementById('run-settings').open=true")
    page.click(f'button[data-loan="{loan}"]')
    page.wait_for_function("document.querySelector('#card-body') && !document.querySelector('#card-body').hidden", timeout=120000)
    page.wait_for_timeout(1500)

BLK = "#trace-pipeline .step-block"
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": VW, "height": VH}, device_scale_factor=DSF)
    page.goto(URL); page.add_style_tag(content="html,body{scroll-behavior:auto !important} #evidence-drawer{align-items:center !important} #evidence-panel{border-radius:14px !important}"); page.wait_for_timeout(1000)
    page.check('input[name="source"][value="recorded_live"]'); page.wait_for_timeout(300)
    page.check('input[name="recorded_variant"][value="nightshift"]')
    shot(page, "src", y=0, hl=[".panel-run fieldset, .source-row", "#recorded-live-sub"])
    run_loan(page, "LN-1042")
    shot(page, "l1042_ask", y=0, hl=["#replay-banner", "#case-header"])
    shot(page, "l1042_scout", "#case-header", off=20, hl=[f"{BLK}:nth-child(1)", "#run-stepper"])
    shot(page, "l1042_ev", f"{BLK}:nth-child(2)", off=120, hl=f"{BLK}:nth-child(2)")
    shot(page, "l1042_bureau", f"{BLK}:nth-child(2)", off=120, hl=f"{BLK}:nth-child(2) details:nth-of-type(2)")
    page.locator("#claims button").nth(2).click(); page.wait_for_timeout(900)
    page.screenshot(path=str(OUT / "l1042_drawer.png")); META["l1042_drawer"] = []
    print("shot drawer", flush=True)
    page.click("#close-evidence"); page.wait_for_timeout(300)
    shot(page, "l1042_draft", f"{BLK}:nth-child(3)", off=120, hl=f"{BLK}:nth-child(3)")
    shot(page, "l1042_ver", f"{BLK}:nth-child(4)", off=120)
    shot(page, "l1042_table", f"{BLK}:nth-child(4) table, #trace-pipeline table", off=300, hl="#trace-pipeline table")
    shot(page, "l1042_drivers", "#whycard-summary", off=140, hl="#summary-drivers")
    shot(page, "l1042_cf", "#whycard-summary", off=140, hl="#summary-counterfactual")
    shot(page, "l1042_letter", "#letter-box", off=150, hl="#letter-box")
    run_loan(page, "LN-1230")
    shot(page, "l1230_head", y=0, hl=["#case-header"])
    shot(page, "l1230_draft", f"{BLK}:nth-child(3)", off=120, hl=f"{BLK}:nth-child(3)")
    shot(page, "l1230_ver", f"{BLK}:nth-child(4)", off=120)
    shot(page, "l1230_card", "#whycard-summary", off=140, hl=["#summary-drivers", "#summary-counterfactual"])
    shot(page, "l1230_rej", ".panel-rejected", off=300, hl=".panel-rejected")
    run_loan(page, "LN-1301")
    shot(page, "l1301_head", y=0, hl=["#case-header"])
    shot(page, "l1301_flag", y=0, hl=["#reason-check-banner"])
    shot(page, "l1301_cf", "#whycard-summary", off=140, hl=["#summary-drivers", "#summary-counterfactual"])
    shot(page, "l1301_letter", "#letter-box", off=150, hl="#letter-box")
    page.evaluate("document.getElementById('run-settings').open=true")
    page.check('input[name="source"][value="verifier_stress"]'); page.wait_for_timeout(2500)
    shot(page, "stress_top", y=0, hl=["#stress-tiles"])
    R = "#stress-table-wrap tbody tr"
    shot(page, "stress_mid", "#stress-table-wrap", off=20)
    shot(page, "stress_r2", "#stress-table-wrap", off=20, hl=f"{R}:nth-child(3)")
    shot(page, "stress_r3", "#stress-table-wrap", off=20, hl=f"{R}:nth-child(1)")
    shot(page, "stress_bot", y=99999)
    b.close()
json.dump(META, open(OUT / "meta.json", "w"))
