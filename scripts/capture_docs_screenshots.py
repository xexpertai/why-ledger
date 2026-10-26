"""Capture docs/screenshots at 1920x1080 (requires server on :8090)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
URL = "http://127.0.0.1:8090"


def open_run_settings(page) -> None:
    page.evaluate(
        "() => { const d = document.getElementById('run-settings'); if (d) d.open = true; }"
    )


def run_loan(page, loan: str) -> None:
    page.click(f'.loan-picks button[data-loan="{loan}"]')
    page.wait_for_function(
        "document.querySelector('#whycard-summary') && !document.querySelector('#whycard-summary').hidden",
        timeout=120000,
    )
    page.wait_for_timeout(800)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(URL)
        page.wait_for_timeout(600)

        page.check('input[name="source"][value="recorded_live"]')
        page.check('input[name="recorded_variant"][value="nightshift"]')
        run_loan(page, "LN-1042")
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "recorded-live-ln-1042.png"))

        run_loan(page, "LN-1230")
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "recorded-live-ln-1230.png"))

        page.evaluate("window.scrollTo(0, document.getElementById('letter-box').offsetTop - 40)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "applicant-letter-ln-1230.png"))

        run_loan(page, "LN-1301")
        page.wait_for_selector("#reason-check-banner:not([hidden])", timeout=120000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "recorded-live-ln-1301.png"))

        page.evaluate("window.scrollTo(0, document.getElementById('letter-box').offsetTop - 40)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "applicant-letter-ln-1301.png"))

        open_run_settings(page)
        page.check('input[name="source"][value="verifier_stress"]')
        page.wait_for_timeout(2000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "verifier-stress.png"))

        browser.close()
    print("Wrote screenshots to", OUT)


if __name__ == "__main__":
    main()
