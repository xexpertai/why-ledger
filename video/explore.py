from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1920,"height":1080})
    pg.goto("http://127.0.0.1:8090"); pg.add_style_tag(content="html,body{scroll-behavior:auto !important}"); pg.wait_for_timeout(1000)
    pg.screenshot(path="video/explore/home.png")
    pg.check('input[name="source"][value="recorded_live"]'); pg.check('input[name="recorded_variant"][value="nightshift"]')
    pg.click('button[data-loan="LN-1042"]'); pg.click("#ask-btn")
    pg.wait_for_function("!document.querySelector('#card-body').hidden",timeout=60000); pg.wait_for_timeout(2500)
    pg.screenshot(path="video/explore/l1042_full.png",full_page=True)
    print(pg.evaluate("document.body.scrollHeight"))
    print(pg.evaluate("[...document.querySelectorAll('section,header,ol,aside')].map(e=>e.id+'|'+e.className+'|'+Math.round(e.getBoundingClientRect().top+scrollY)+'|'+Math.round(e.getBoundingClientRect().height)).join('\\n')"))
    b.close()
