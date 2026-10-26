"""Kokoro af_heart narration (CPU, ~/workspace/.video-venv). Writes build/vo/<id>_<k>.wav/.txt (k = ORIGINAL sentence index; cut ones skipped).
Run: systemd-run --user --scope -p MemoryMax=3G flock /tmp/heavy.lock ~/workspace/.video-venv/bin/python video/tts_kokoro.py"""
import json, re, os, numpy as np, soundfile as sf
from pathlib import Path
from kokoro import KPipeline
V = Path(__file__).parent; OUT = V / "build/vo"
import shutil; shutil.rmtree(OUT, ignore_errors=True); OUT.mkdir(parents=True)
SPEED = float(os.environ.get("SPEED", "0.85"))
RESP = [("Zeh-tah-riss", "[Zetaris](/zˈɛtɑɹˌɪs/)"), ("Shinde", "[Shinde](/ʃˈɪndeɪ/)"), ("Meter-less", "Meter less"), ("Nemotron", "[Nemotron](/nˈɛmoʊtɹɑn/)"), ("Why-Card", "Why Card"), ("Why Ledger", "Why Ledger")]
def tts_text(t):
    for a, b in RESP: t = t.replace(a, b)
    return t
pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
for sc in json.load(open(V / "story.json")):
    for k, s in enumerate(x.strip() for x in re.findall(r"[^.]+\.?", sc["text"]) if x.strip()):
        if k in sc.get("cut", []): continue
        w = np.concatenate([np.asarray(x) for _, _, x in pipe(tts_text(s), voice="af_heart", speed=SPEED)])
        sf.write(OUT / f"{sc['id']}_{k}.wav", w, 24000); (OUT / f"{sc['id']}_{k}.txt").write_text(s)
        print(sc["id"], k, round(len(w) / 24000, 2), len(s.split()), flush=True)
