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
    run_loan(page, "LN-1042")
    shot(page, "l1042_scout", f"{BLK}:nth-child(1)", off=120, hl=[f"{BLK}:nth-child(1)"])
    b.close()
m = json.load(open(OUT / "meta.json")); m.update(META); json.dump(m, open(OUT / "meta.json", "w"))
