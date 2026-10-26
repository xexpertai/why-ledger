"""Build video/why-ledger-demo.mp4 from UI stills (shots/), PIL cards and the Kokoro TTS narration (build/vo)."""
import json, re, subprocess, glob
from pathlib import Path
import numpy as np, soundfile as sf
from PIL import Image, ImageDraw, ImageFont

V = Path(__file__).parent; B = V / "build"; SH = V / "shots"; VO = B / "vo"
FD = "/usr/share/fonts/truetype/noto/"
def F(sz, bold=False): return ImageFont.truetype(FD + ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"), sz)
def M(sz): return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", sz)
W, H = 1920, 1080
BG, FG, MUT, ACC, OK = (15, 22, 36), (232, 238, 248), (150, 166, 190), (77, 163, 255), (74, 222, 128)
BAR = "SYNTHETIC demo data. Harbor Credit is a fictional lender."
SR = 24000; GAP_S = 0.34; GAP_SC = 0.25
meta = json.load(open(SH / "meta.json"))

def bar(img):
    d = ImageDraw.Draw(img); d.rectangle([0, H - 50, W, H], fill=(40, 28, 8))
    d.text((W // 2, H - 25), BAR, font=F(24, True), fill=(250, 190, 90), anchor="mm"); return img

APP_H = 895; STRIP_H = H - 50 - APP_H  # app screenshots are cropped above the UI's own SYNTHETIC footer (y 895), so only ONE SYNTHETIC bar (the video bar) shows  # app viewport is scaled into [0, APP_H); captions live only in the reserved strip

def caption(img, text, top=False):
    """Draw the caption pill in the reserved strip (never over app content). `top` is ignored (legacy)."""
    if not text: return img
    d = ImageDraw.Draw(img); f = F(34, True); w = d.textlength(text, font=f)
    assert w + 48 < W - 40, text
    x0 = (W - w) / 2 - 24; y0 = APP_H + (STRIP_H - 60) // 2
    d.rounded_rectangle([x0, y0, x0 + w + 48, y0 + 60], 14, fill=(16, 78, 60), outline=(74, 222, 128), width=2)
    d.text((x0 + 24 + w / 2, y0 + 30), text, font=f, fill=(255, 255, 255), anchor="mm"); return img

def shot(name, cap="", boxes=True, top=False):
    img = Image.open(SH / f"{name}.png").convert("RGB"); d = ImageDraw.Draw(img)
    if boxes:
        for x, y, w, h in meta.get(name, []):
            d.rounded_rectangle([x - 6, y - 6, x + w + 6, y + h + 6], 10, outline=(255, 160, 40), width=5)
    if img.height > APP_H: img = img.crop((0, 0, img.width, APP_H))
    im = img
    can = Image.new("RGB", (W, H), (8, 12, 22)); can.paste(im, ((W - im.width) // 2, 0))
    ImageDraw.Draw(can).rectangle([0, APP_H, W, H - 50], fill=(10, 16, 28))
    return caption(bar(can), cap, top)

def base(): return Image.new("RGB", (W, H), BG)
def ctext(d, y, t, f, fill=FG): d.text((W // 2, y), t, font=f, fill=fill, anchor="mm")

def card_title():
    i = base(); d = ImageDraw.Draw(i)
    ctext(d, 340, "Why-Ledger", F(150, True)); ctext(d, 470, "Track 2: The Agent That Can Explain Why", F(48), ACC)
    ctext(d, 600, "Harsha Shinde", F(56, True)); ctext(d, 690, "Open Agent Hackathon 2026", F(36), MUT)
    ctext(d, 790, "SYNTHETIC data · fictional lender Harbor Credit", F(38, True), (250, 190, 90)); return bar(i)

def card_boxes():
    i = base(); d = ImageDraw.Draw(i)
    ctext(d, 150, "The real reasons sit in several systems", F(54, True))
    names = [("Applications", "amount, term, sector"), ("Payments", "late counts, conduct"), ("Bureau", "score, utilisation"), ("Policy rules", "reason codes, scorecard")]
    for k, (n, s) in enumerate(names):
        x = 130 + k * 440
        d.rounded_rectangle([x, 330, x + 380, 560], 20, fill=(24, 36, 58), outline=ACC, width=3)
        ctext_at = lambda y, t, f, c=FG: d.text((x + 190, y), t, font=f, fill=c, anchor="mm")
        ctext_at(420, n, F(44, True)); ctext_at(490, s, F(28), MUT)
    d.line([960, 560, 960, 650], fill=ACC, width=4)
    d.rounded_rectangle([420, 650, 1500, 790], 20, fill=(16, 78, 60), outline=OK, width=3)
    ctext(d, 720, "Every claim checked against the data before it is shown", F(40, True))
    ctext(d, 900, "SYNTHETIC data. All four boxes are fictional Harbor Credit tables.", F(30), MUT); return bar(i)

def card_term():
    out = (B / "conn.txt").read_text().strip(); assert "PASS" in out and not re.search(r"key|token|secret|password|sk-|nvapi", out, re.I)
    i = base(); d = ImageDraw.Draw(i)
    d.rounded_rectangle([170, 200, 1750, 760], 18, fill=(8, 10, 16), outline=(70, 82, 105), width=2)
    d.rectangle([170, 200, 1750, 250], fill=(30, 38, 54)); d.text((200, 225), "terminal · hk-openagent", font=F(26), fill=MUT, anchor="lm")
    d.text((210, 320), "$ python scripts/test_connection.py", font=M(34), fill=FG)
    d.text((210, 400), out.split(" model=")[0], font=M(44), fill=OK)
    d.text((210, 470), "model=" + out.split(" model=")[1].split(" nvidia_models")[0], font=M(34), fill=FG)
    d.text((210, 530), "nvidia_models_listed=" + out.rsplit("=", 1)[1], font=M(34), fill=FG)
    d.text((210, 650), "Provider: Meterless Nightshift (no key is printed or shown)", font=F(30), fill=MUT)
    ctext(d, 870, "Connection test output, run for this video", F(32), MUT); return bar(i)

def card_banner():
    i = base(); d = ImageDraw.Draw(i)
    ctext(d, 380, "Zetaris live + Nemotron via Meterless Nightshift", F(66, True), OK)
    ctext(d, 500, "nvidia/nemotron-3.5-lightning-30b-a3b", F(44), FG)
    ctext(d, 600, "Primary provider: Meterless Nightshift", F(38), ACC)
    ctext(d, 760, "Recorded 25 Oct 2026 · provider path from eval/results-live-nightshift.md", F(30), MUT); return bar(i)

def card_eval():
    i = base(); d = ImageDraw.Draw(i)
    ctext(d, 110, "eval/results-live-nightshift.md + replay bundles (reason check)", F(44, True), ACC)
    ctext(d, 170, "One Nightshift run per loan, recorded 25 Oct 2026 (SYNTHETIC)", F(30), MUT)
    cols = [("Loan", 240), ("Drafted", 560), ("Verified", 780), ("Rejected", 1000), ("Top driver = truth", 1300), ("Latency", 1620)]
    rows = [("LN-1042", "6", "6", "0", "yes", "10.2 s"), ("LN-1107", "5", "5", "0", "yes", "6.7 s"), ("LN-1230", "6", "6", "0", "yes", "7.8 s"), ("LN-1301", "5", "5", "0", "yes", "6.8 s")]
    for (n, x) in cols: d.text((x, 270), n, font=F(32, True), fill=MUT, anchor="mm")
    for r, row in enumerate(rows):
        for (n, x), v in zip(cols, row): d.text((x, 350 + r * 70), v, font=F(36), fill=FG, anchor="mm")
    d.line([120, 620, 1800, 620], fill=(70, 82, 105), width=2)
    for (n, x), v in zip(cols, ("Total", "22", "22", "0", "4/4", "")): d.text((x, 680), v, font=F(40, True), fill=OK if v else FG, anchor="mm")
    ctext(d, 790, "Top driver matched the planted truth: 4 of 4.  22 drafted, 22 verified, 0 rejected.", F(38, True)); ctext(d, 880, "Recorded-reason check: 1 of 1 planted mismatch flagged (LN-1301), 0 false flags on the other 3.", F(34, True), OK); return bar(i)

def card_end():
    i = base(); d = ImageDraw.Draw(i)
    ctext(d, 230, "Why-Ledger", F(130, True)); ctext(d, 350, "Harsha Shinde", F(60, True), ACC)
    ctext(d, 470, "https://github.com/xexpertai/why-ledger", F(46), FG)
    ctext(d, 600, "SYNTHETIC data · fictional lender Harbor Credit", F(40, True), (250, 190, 90))
    ctext(d, 710, "Built in Cursor; directed and reviewed with Claude Code", F(38), FG)
    ctext(d, 790, "Narration: AI text-to-speech voice (Kokoro)", F(38), FG)
    ctext(d, 870, "Zetaris · Meterless Scout Intent · Meterless Nightshift · NVIDIA Nemotron", F(30), MUT); return bar(i)

def card_points(n):
    """Opening card: title + six points, revealed up to n."""
    i = base(); d = ImageDraw.Draw(i)
    d.text((110, 90), "Why-Ledger", font=F(84, True), fill=FG, anchor="lm")
    d.text((110, 170), "Track 2: The Agent That Can Explain Why  ·  Harsha Shinde", font=F(32), fill=ACC, anchor="lm")
    pts = [("The problem", "Credit decisions are hard to explain; regulators and applicants need reasons."),
           ("Who uses it", "Credit analysts and compliance teams at a lender."),
           ("What it does", "Answers \"why was this loan declined?\" from live data."),
           ("Why trust it", "Every claim is re-checked against Zetaris by re-running its SQL. Unsupported claims never ship."),
           ("The benefit", "An evidence-backed explanation, plus a counterfactual: what small change would flip the decision."),
           ("Where it runs", "Zetaris + Meterless Nightshift + NVIDIA Nemotron on my own machine. Built in Cursor.")]
    for k, (h, t) in enumerate(pts):
        y = 250 + k * 118; on = k < n; cur = k == n - 1
        col = FG if on else (70, 82, 105)
        d.rounded_rectangle([110, y, 1810, y + 100], 14, fill=(24, 36, 58) if on else (18, 26, 42), outline=OK if cur else (60, 74, 98), width=4 if cur else 2)
        d.text((150, y + 50), str(k + 1), font=F(48, True), fill=ACC if on else (70, 82, 105), anchor="mm")
        d.text((210, y + 28), h, font=F(30, True), fill=OK if on else col, anchor="lm")
        d.text((210, y + 70), t, font=F(30), fill=col, anchor="lm")
    d.text((W // 2, 985), "SYNTHETIC data  ·  fictional lender Harbor Credit", font=F(28), fill=(250, 190, 90), anchor="mm")
    return bar(i)

def card_limits():
    i = base(); d = ImageDraw.Draw(i)
    ctext(d, 130, "Limits, stated honestly", F(64, True), ACC)
    items = ["All data is SYNTHETIC; the lender Harbor Credit is fictional.", "Free-tier latency varies: 6.7 to 10.2 s here, up to 534 s earlier.",
             "One Nemotron model works on this Nightshift account.", "One run per loan: no variance claim.", "Next: more loans and more policies."]
    for k, t in enumerate(items):
        d.rounded_rectangle([210, 250 + k * 120, 1710, 340 + k * 120], 14, fill=(24, 36, 58), outline=(60, 74, 98), width=2)
        assert d.textlength(t, font=F(36)) < 1380, t
        d.text((260, 295 + k * 120), t, font=F(36), fill=FG, anchor="lm")
    return bar(i)

# scene plan: (id, [(image, first sentence index NOT covered)]) ; the last entry always runs to the end
def P(n): return lambda: card_points(n)
PLAN = [
 ("s01", [(shot("l1301_flag", "The problem: a lender's own recorded reason can be wrong"), 1), (shot("l1042_ask", "For credit analysts and compliance teams"), 2), (shot("l1042_ver", "Every claim re-checked against Zetaris; unsupported claims never ship"), 3), (shot("l1042_cf", "Counterfactual: what small change flips the decision"), 4), (shot("l1042_ev", "Runs on my machine: Zetaris, Meterless Nightshift, NVIDIA Nemotron"), 99)]),
 ("s02", [(shot("src", "Recorded live run, not running now"), 2), (shot("l1042_ask", "Nightshift run (25 Oct): replay, original latency 10.2 s"), 99)]),
 ("s03", [(shot("l1042_scout", "Step 1: Meterless Scout Intent"), 99)]),
 ("s04", [(shot("l1042_ev", "Step 2: Zetaris, live JDBC, read-only SQL"), 2), (shot("l1042_bureau", "Step 2: the bureau query, one of six"), 3), (shot("l1042_drawer", "Bureau rows: utilisation 91, score 670; row hash logged", True, True), 99)]),
 ("s05", [(shot("l1042_draft", "Step 3: Nemotron drafts 6 claims"), 1), ("banner", 2), ("term", 99)]),
 ("s06", [(shot("l1042_ver", "Step 4: verifier re-runs each cited query"), 2), (shot("l1042_table", "6 drafted, 6 VERIFIED", False), 99)]),
 ("s07", [(shot("l1042_drivers", "Drivers ranked from scorecard points, not the LLM"), 1), (shot("l1042_cf", "Counterfactual: what would flip the decision"), 2), (shot("l1042_letter", "Applicant letter: ranked principal reasons (SYNTHETIC)"), 99)]),
 ("s08", [(shot("l1230_draft", "LN-1230: 6 drafted"), 1), (shot("l1230_ver", "LN-1230 verifier: 6 claims, all shipped on card", False), 3), (shot("l1230_card", "LN-1230: 6 verified, 0 rejected; #1 driver amount-to-income"), 99)]),
 ("s08b", [(shot("l1301_head", "LN-1301: record says INCOME_BELOW_THRESHOLD"), 2), (shot("l1301_flag", "Recorded reason not supported by data"), 4), (shot("l1301_cf", "Data-supported drivers; counterfactual: bureau score 580"), 99)]),
 ("s09", [(shot("stress_top", "PLANTED test claims, not model output"), 2), (shot("stress_r2", "Planted false value: UNSUPPORTED", True, "gap"), 3), (shot("stress_r3", "Planted wrong loan scope: UNSUPPORTED", True, "gap"), 4), (shot("stress_mid", "14 cases: 4 VERIFIED, 10 UNSUPPORTED; verdict 14/14, reason 14/14", False, "gap"), 99)]),
 ("s10", [("eval", 99)]),
 ("s11", [("limits", 99)]),
 ("s12", [("end", 99)]),
]
CARDS = {"banner": card_banner, "term": card_term, "eval": card_eval, "end": card_end, "limits": card_limits}

def run(*a): subprocess.run(a, check=True)

def sents(text):
    return [x.strip() for x in re.findall(r"[^.]+\.?", text) if x.strip()]

def capq(a, thr, cap=0.30):
    """Cap any internal near-silent run (Kokoro pauses at commas/colons) to `cap` s so no pause exceeds ~0.6 s with the gap."""
    q = np.abs(a) < thr; keep = np.ones(len(a), bool); i = 0; m = int(cap * SR)
    while i < len(a):
        if q[i]:
            j = i
            while j < len(a) and q[j]: j += 1
            if j - i > m: keep[i + m:j] = False
            i = j
        else: i += 1
    return a[keep]

def build():
    story = json.load(open(V / "story.json")); (B / "seg").mkdir(exist_ok=True)
    track_parts, total, segs = [], 0.0, []
    lead = 0.3
    for si, sc in enumerate(story):
        allv = sents(sc["text"]); keep = [k for k in range(len(allv)) if k not in sc.get("cut", [])]; n = len(keep); auds = []
        for k in keep:
            a, sr = sf.read(VO / f"{sc['id']}_{k}.wav", dtype="float32"); assert sr == SR, sr
            assert (VO / f"{sc['id']}_{k}.txt").read_text().split("\n")[0] == allv[k]
            thr = 10 ** (-34 / 20) * np.abs(a).max(); nz = np.flatnonzero(np.abs(a) > thr)
            auds.append(capq(a[max(0, nz[0] - 480): nz[-1] + 720], 10 ** (-40 / 20) * np.abs(a).max()))
        t0 = total + (lead if si == 0 else 0)
        starts, t = [], 0.0
        for a in auds: starts.append(t); t += len(a) / SR + GAP_S
        aud = np.zeros(int((t - GAP_S) * SR) + 4, np.float32)
        for st, a in zip(starts, auds): aud[int(st * SR): int(st * SR) + len(a)] = a
        dur = len(aud) / SR + (lead if si == 0 else 0) + GAP_SC + (-GAP_SC + 0.15 if si == len(story) - 1 else 0)
        track_parts.append((t0, aud))
        plan = dict(PLAN)[sc["id"]]; prev = 0.0
        for k, (img, upto) in enumerate(plan):
            u = sum(1 for q in keep if q < upto)  # plan indices are ORIGINAL sentence indices
            end = dur if (k == len(plan) - 1 or u >= n) else (starts[u] - GAP_S / 2) + (lead if si == 0 else 0)
            if end - prev < 0.3:  # visual whose sentence was cut: drop it, give its time to the previous visual
                if k == len(plan) - 1 and segs and end > prev: segs[-1] = (segs[-1][0], segs[-1][1] + end - prev)
                continue
            png = B / "seg" / f"{sc['id']}_{k}.png"
            im = img() if callable(img) else (CARDS[img]() if isinstance(img, str) else img)
            im.save(png); segs.append((png, end - prev)); prev = end
        total += dur
    # video: one clip per still with short fades
    lst = []
    for n, (png, d) in enumerate(segs):
        out = B / "seg" / f"c{n:02d}.mp4"
        run("ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-t", f"{d:.3f}", "-i", str(png), "-vf",
            "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "17", str(out))
        lst.append(f"file '{out}'\n")
    (B / "concat.txt").write_text("".join(lst))
    run("ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(B / "concat.txt"), "-c", "copy", str(B / "video.mp4"))
    tr = np.zeros(int(total * SR) + SR, np.float32)
    for t, a in track_parts: tr[int(t * SR): int(t * SR) + len(a)] += a
    sf.write(B / "vo.wav", tr[: int(total * SR)], SR, subtype="PCM_16")
    m = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(B / "vo.wav"), "-af", "loudnorm=I=-13.4:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    j = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", m).group(0))
    af = (f"loudnorm=I=-13.4:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    run("ffmpeg", "-loglevel", "error", "-y", "-i", str(B / "video.mp4"), "-i", str(B / "vo.wav"), "-af", af + ",aresample=48000",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(V / "why-ledger-demo.mp4"))
    print("total", round(total, 1))

if __name__ == "__main__": build()
