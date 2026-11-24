"""Self-test: runs the WHOLE pipeline in a temporary sandbox with simulators.

- fake music engine (generates synthetic audio), fake Ollama, fake YouTube API - nothing leaves the PC,
- real ffmpeg, real image and audio processing, real file handling,
- checks the rules that must never break (AI label in title, description and API flag; no double upload).

Used by the repair loop: a code change is kept ONLY if this test passes.
    python app/selftest.py          (or: AIMusicAgent.exe --selftest)
Exit code 0 = all checks passed.
"""
from __future__ import annotations

import http.server
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

APP = Path(__file__).resolve().parent

FAKE_SA3 = r'''
import sys, wave, struct, math, random
a = sys.argv[1:]
if "--help" in a:
    print("usage: sa3-generate --model M --prompt P --duration S --out F --seed N"); sys.exit(0)
g = lambda k: a[a.index(k) + 1]
out = g("--out"); dur = min(int(g("--duration")), 20); seed = int(g("--seed")) if "--seed" in a else 1
rnd = random.Random(seed); sr = 22050
notes = [rnd.choice([110, 130.8, 146.8, 164.8, 196, 220]) for _ in range(8)]
w = wave.open(out, "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
fr = bytearray()
for i in range(sr * dur):
    t = i / sr; f = notes[int(t * 2) % 8]
    v = min(1, t / 2) * 0.3 * (math.sin(2 * math.pi * f * t) + 0.4 * math.sin(2 * math.pi * f * 1.5 * t))
    s = int(max(-1, min(1, v)) * 32767); fr += struct.pack("<hh", s, s)
w.writeframes(bytes(fr)); w.close()
'''

FAKE_ACE_LM = r'''
import json, os, sys
a = sys.argv[1:]
if "--help" in a:
    print("usage: ace-lm --models DIR --request FILE [--threads N]"); sys.exit(0)
req = a[a.index("--request") + 1]
r = json.load(open(req, encoding="utf-8"))
assert r.get("lyrics") == "[Instrumental]", "instrumental only"
models = a[a.index("--models") + 1]
if r.get("lm_model") and not os.path.exists(os.path.join(models, r["lm_model"])):
    sys.stderr.write("[Ace-LM] FATAL: lm_model '%s' not found in registry\n" % r["lm_model"]); sys.exit(1)
r.update(bpm=r.get("bpm") or 90, keyscale=r.get("keyscale") or "A minor", audio_codes="1,2,3")
json.dump(r, open(req.replace("request.json", "request0.json"), "w"))
'''

FAKE_SD = r'''
import random, sys
from PIL import Image, ImageFilter
a = sys.argv
w, h, out = int(a[a.index("-W") + 1]), int(a[a.index("-H") + 1]), a[a.index("-o") + 1]
rnd = random.Random(int(a[a.index("-s") + 1]))
im = Image.effect_noise((w, h), 60).convert("RGB")
im = Image.merge("RGB", [c.point(lambda v, k=rnd.randint(40, 200): (v + k) % 256) for c in im.split()])
im.filter(ImageFilter.GaussianBlur(2)).save(out)
'''

FAKE_ACE_SYNTH = r'''
import json, sys, wave, struct, math, random, os
a = sys.argv[1:]
if "--help" in a:
    print("usage: ace-synth --models DIR --request FILE [--threads N]"); sys.exit(0)
req = a[a.index("--request") + 1]
r = json.load(open(req, encoding="utf-8"))
models = a[a.index("--models") + 1]
assert "peak_clip" in r, "peak_clip must be sent (engine default clips peaks)"
if r.get("synth_model") and not os.path.exists(os.path.join(models, r["synth_model"])):
    sys.stderr.write("[Ace-Synth] FATAL: synth_model '%s' not found in registry\n" % r["synth_model"]); sys.exit(1)
dur = min(int(r["duration"]), 60); rnd = random.Random(r["seed"]); sr = 22050
notes = [rnd.choice([110, 130.8, 146.8, 164.8, 196, 220]) for _ in range(8)]
out = os.path.join(os.path.dirname(req), os.path.basename(req).replace(".json", "0.wav"))
w = wave.open(out, "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
fr = bytearray()
for i in range(sr * dur):
    t = i / sr; f = notes[int(t * 2) % 8]
    v = min(1, t / 2) * 0.3 * (math.sin(2 * math.pi * f * t) + 0.4 * math.sin(2 * math.pi * f * 1.5 * t))
    s = int(max(-1, min(1, v)) * 32767); fr += struct.pack("<hh", s, s)
w.writeframes(bytes(fr)); w.close()
'''

CAPTURE: dict = {"uploads": []}


class FakeOllama(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/version":
            return self._j({"version": "selftest"})
        if self.path == "/api/tags":
            return self._j({"models": [{"name": "qwen2.5:3b"}]})
        self._j({}, 404)

    def do_POST(self):  # noqa: N802
        req = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if b"Translate these texts" in req:
            return self._j({"response": json.dumps({"phrase": "Entspannende Musik", "story": "Ein Test.", "perfect_for": "Lernen"})})
        brief = {"substyle": "chillwave", "mood": "dreamy", "bpm": 96, "instruments": ["synth", "drum machine"],
                 "music_prompt": "dreamy chillwave, 96 BPM, warm synth pads, soft drums, intro build climax outro",
                 "visual_theme": {"palette": ["#1B0B3A", "#FF5DA2", "#3DE0F5"], "motif": "neon horizon"},
                 "title": "Selftest Horizon", "description_story": "A test track.", "hashtags": ["#chill"],
                 "trend_reason": "Testing."}
        self._j({"response": json.dumps(brief)})


class FakeYouTube(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, o, code=200, headers=None):
        b = json.dumps(o).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self):  # noqa: N802
        if "chart=mostPopular" in self.path:
            return self._j({"items": [{"snippet": {"title": "Epic cinematic soundtrack", "tags": ["epic"]},
                                       "statistics": {"viewCount": "1000"}}]})
        if self.path.startswith("/youtube/v3/channels"):
            return self._j({"items": [{"id": "UCselftest", "snippet": {"title": "Selftest Channel", "customUrl": "@selftestchannel"}}]})
        if self.path.startswith("/youtube/v3/search"):
            return self._j({"items": [{"id": {"videoId": f"S{i}"}} for i in range(3)]})
        if self.path.startswith("/youtube/v3/videos"):
            return self._j({"items": [{"id": f"S{i}", "statistics": {"viewCount": str(1000 * (i + 1))}} for i in range(3)]})
        if self.path.startswith("/youtube/v3/playlists"):
            return self._j({"items": []})
        if self.path.startswith("/v2/reports"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            ids = q.get("filters", ["video=="])[0].split("==", 1)[1].split(",")
            mets = q.get("metrics", [""])[0].split(",")
            if "videoThumbnailImpressions" in mets:
                return self._j({"error": {"message": "Unknown identifier", "errors": [{"reason": "badRequest"}]}}, 400)
            return self._j({"columnHeaders": [{"name": "video"}] + [{"name": m} for m in mets],
                            "rows": [[v] + [42 + i for i in range(len(mets))] for v in ids if v]})
        self._j({}, 404)

    def do_POST(self):  # noqa: N802
        b = self._body()
        if self.path == "/token":
            return self._j({"access_token": "AT", "expires_in": 3600})
        if self.path.startswith("/upload/youtube/v3/videos"):
            CAPTURE["uploads"].append(json.loads(b))
            port = self.server.server_port
            return self._j({}, 200, {"Location": f"http://127.0.0.1:{port}/session/{len(CAPTURE['uploads'])}"})
        if self.path.startswith("/upload/youtube/v3/thumbnails"):
            return self._j({})
        if self.path.startswith("/youtube/v3/playlists"):
            return self._j({"id": "PLtest"})
        if self.path.startswith("/youtube/v3/playlistItems"):
            return self._j({"id": "PItest"})
        self._j({}, 404)

    def do_PUT(self):  # noqa: N802
        b = self._body()
        if self.path.startswith("/youtube/v3/playlists"):
            CAPTURE.setdefault("renamed", []).append(json.loads(b))
            return self._j({"id": "PLold"})
        m = re.match(r"bytes (\d+)-(\d+)/(\d+)", self.headers.get("Content-Range", ""))
        if m and int(m.group(2)) + 1 < int(m.group(3)):
            self.send_response(308)
            self.send_header("Range", f"bytes=0-{m.group(2)}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._j({"id": f"TEST{len(CAPTURE['uploads'])}"})


def serve(handler) -> http.server.ThreadingHTTPServer:
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def main() -> int:
    checks: list[tuple[bool, str]] = []

    def ok(cond: bool, text: str) -> None:
        checks.append((bool(cond), text))

    # 1. every module compiles
    bad = []
    for f in APP.rglob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{f.name}: {e.msg}")
    ok(not bad, "all Python files compile" + (f" ({'; '.join(bad)})" if bad else ""))
    if bad:
        return report(checks)

    ollama, yt = serve(FakeOllama), serve(FakeYouTube)
    tmp = Path(tempfile.mkdtemp(prefix="aimusic-selftest-"))
    try:
        return _scenario(tmp, ollama, yt, ok, checks)
    except Exception as e:  # the selftest itself must always end with a report
        ok(False, f"selftest crashed: {e!r}")
        return report(checks)
    finally:
        ollama.shutdown()
        yt.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


def _scenario(tmp: Path, ollama, yt, ok, checks) -> int:
    if True:
        (tmp / "config").mkdir()
        (tmp / "secrets").mkdir()
        fake = tmp / "fake_sa3.py"
        fake.write_text(FAKE_SA3, encoding="utf-8")
        acebin, acemod = tmp / "acebin", tmp / "acemodels"
        sdmod = tmp / "sdmodels"
        sdmod.mkdir()
        (tmp / "fake_sd.py").write_text(FAKE_SD, encoding="utf-8")
        for name in ("flux-2-klein-4b-Q4_0.gguf", "Qwen3-4B-Q4_K_M.gguf", "flux2-vae.safetensors"):
            (sdmod / name).write_bytes(b"x")
        acebin.mkdir()
        acemod.mkdir()
        (acebin / "ace-lm.exe").write_text(FAKE_ACE_LM, encoding="utf-8")
        (acebin / "ace-synth.exe").write_text(FAKE_ACE_SYNTH, encoding="utf-8")
        for name in ("acestep-v15-turbo-Q8_0", "acestep-5Hz-lm-1.7B-Q8_0", "vae-BF16", "Qwen3-Embedding-0.6B-Q8_0"):
            (acemod / f"{name}.gguf").write_bytes(b"x")
        cfg = {
            "llm": {"host": f"http://127.0.0.1:{ollama.server_port}", "force_cpu": False},
            "acestep": {"lm_cmd": [sys.executable, "{lm}", "--models", "{models}", "--request", "{request}"],
                        "synth_cmd": [sys.executable, "{synth}", "--models", "{models}", "--request", "{request}"]},
            "music": {"engine": "acestep", "min_seconds": 30, "max_seconds": 40, "section_max_seconds": 15, "crossfade_seconds": 2, "variants": 2,
                      "command": [sys.executable, "{exe}", "--model", "{model}", "--prompt", "{prompt}",
                                  "--duration", "{duration}", "--out", "{out}"]},
            "video": {"width": 640, "height": 360},
            "shorts": {"seconds": 10},
            "images": {"command": [sys.executable, str(tmp / "fake_sd.py")], "wide": [512, 256], "tall": [256, 512]},
            "bonus": {"enabled": True, "min_score": 0.0, "max_score_gap": 1.0, "min_distance": 0.0001},
            "trends": {"regions": ["US"]},
            "repair": {"auto": False},
        }
        (tmp / "config" / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")  # JSON is valid YAML
        (tmp / "secrets" / "client_secret.json").write_text('{"installed":{"client_id":"x","client_secret":"y"}}')
        (tmp / "secrets" / "token.json").write_text('{"refresh_token":"r","expires_at":0}')
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AIMUSIC_", "SA3_"))}
        env.update(AIMUSIC_HOME=str(tmp), AIMUSIC_SA3_EXE=str(fake), PYTHONUTF8="1",
                   AIMUSIC_ACE_BIN=str(acebin), AIMUSIC_ACE_MODELS=str(acemod), AIMUSIC_SD_MODELS=str(sdmod),
                   AIMUSIC_SD_BIN=str(tmp / "no-sd-bin"),
                   AIMUSIC_YT_API_BASE=f"http://127.0.0.1:{yt.server_port}",
                   AIMUSIC_OAUTH_TOKEN_URL=f"http://127.0.0.1:{yt.server_port}/token",
                   AIMUSIC_YTA_API_BASE=f"http://127.0.0.1:{yt.server_port}",
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        if os.environ.get("AIMUSIC_FFMPEG"):
            env["AIMUSIC_FFMPEG"] = os.environ["AIMUSIC_FFMPEG"]
        runpy = str(APP / "run.py")

        def run(*args) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, runpy, "--no-repair", *args], env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=900)

        # 2. dry run: everything except the upload
        p = run("--dry-run")
        out = next((tmp / "output").glob("*"), None)
        ok(p.returncode == 0, "dry run finished with exit code 0" + ("" if p.returncode == 0 else f"\n{p.stdout[-1500:]}"))
        for f in ("trends.json", "brief.json", "track.wav", "cover.png", "thumb.jpg", "video.mp4", "short.mp4"):
            ok(out is not None and (out / f).exists() and (out / f).stat().st_size > 0, f"dry run produced {f}")
        ok(not CAPTURE["uploads"], "dry run uploaded nothing")
        pj = tmp / "state" / "progress.json"
        pr = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}
        ok(pr.get("finished") and pr.get("ok") and pr.get("overall") == 1.0 and len(pr.get("steps", [])) >= 6
           and "%" in p.stdout and "zostało ok." in p.stdout, "progress bar + time estimate (state/progress.json, console)")
        if out is not None and (out / "brief.json").exists():
            br = json.loads((out / "brief.json").read_text(encoding="utf-8"))
            q = json.loads((out / "quality.json").read_text(encoding="utf-8")) if (out / "quality.json").exists() else {}
            ok(len(br.get("sections", [])) == 1, f"ACE-Step: whole track in one generation ({len(br.get('sections', []))} section)")
            d = float(q.get("duration") or 0)
            ok(30 * 0.9 <= d <= 40 + 1, f"track length inside min..max ({d:.1f} s)")
            ms = q.get("mastering") or {}
            ok("gain_db" in ms and abs(float(ms["gain_db"])) <= 20, f"two-pass mastering with constant gain ({ms.get('gain_db')} dB)")
            ok(bool(br.get("substyle")) and bool(br.get("style_pick")), f"style chosen by research ({br.get('substyle')})")
            tr = json.loads((out / "trends.json").read_text(encoding="utf-8"))
            ok(len(tr.get("substyle_demand", {})) > 0, "trend research measured demand per style")

        # 2b. sound quality A/B test renders from the saved song plan
        p = run("--ab-test")
        ab = next((tmp / "output").glob("ab-test-*"), None)
        need = ["1_STARE_ustawienia.wav", "2_NOWE_ustawienia.wav", "3_NOWE_po_kodowaniu_YouTube_AAC384.m4a", "OPIS.txt"]
        ok(p.returncode == 0 and ab is not None and all((ab / n).exists() for n in need),
           "A/B quality test (old vs new sound settings)" + ("" if p.returncode == 0 else f"\n{(p.stdout + p.stderr)[-1200:]}"))
        if ab is not None:
            shutil.rmtree(ab, ignore_errors=True)  # keep the output folder layout of the other checks

        # 3. real run against the fake YouTube (an old-named playlist is in the cache -> must be renamed, not duplicated)
        (tmp / "state").mkdir(exist_ok=True)
        (tmp / "state" / "playlists.json").write_text(json.dumps({"AI Chill & Lofi Music": "PLold"}), encoding="utf-8")
        p = run()
        ok(p.returncode == 0, "upload run finished with exit code 0" + ("" if p.returncode == 0 else f"\n{p.stdout[-1500:]}"))
        ups = CAPTURE["uploads"]
        ok(len(ups) >= 1, "video was uploaded")
        if ups:
            v = ups[0]
            sn, st = v.get("snippet", {}), v.get("status", {})
            ok(not sn.get("title", "").endswith("| AI Music"), "no ' | AI Music' suffix by default (2.6)")
            desc = sn.get("description", "")
            ok("sub_confirmation=1" in desc and "@selftestchannel" in desc.lower() and "playlist?list=" in desc,
               "description: subscribe link + playlist link")
            ok(st.get("containsSyntheticMedia") is True, "API flag containsSyntheticMedia = true")
            loc = v.get("localizations") or {}
            ok("de" in loc and "Entspannende Musik" in loc["de"].get("title", ""),
               f"title/description translated (localizations: {sorted(loc)})")
            ok(re.match(r"^\S+ .+ – Selftest Horizon$", sn.get("title", "")) is not None,
               f"title starts with the search phrase ({sn.get('title')})")
            fam_chill = "Lofi" in sn.get("title", "") or "Jazzhop" in sn.get("title", "") or "Chillstep" in sn.get("title", "") \
                or "Smooth Jazz" in sn.get("title", "")
            ren = CAPTURE.get("renamed", [])
            ok((not fam_chill) or any(r.get("id") == "PLold" and "Lofi & Chill Beats" in r["snippet"]["title"] for r in ren),
               f"old 'AI ...' playlist renamed, not duplicated ({len(ren)} rename)")
            ok(st.get("selfDeclaredMadeForKids") is False, "made-for-kids = false")
            ok(st.get("privacyStatus") in ("private", "public"), "privacy status set")
            ok(len(sn.get("title", "")) <= 100, "title <= 100 characters")
        hf = tmp / "state" / "history.json"
        hist = json.loads(hf.read_text(encoding="utf-8")) if hf.exists() else []
        ok(len([h for h in hist if not h.get("bonus")]) == 1 and bool(hist[0].get("video_id")), "history.json has the uploaded video")

        shorts = [u for u in ups if "#Shorts" in u.get("snippet", {}).get("title", "")]
        st_titles = {u["snippet"]["title"] for u in shorts}
        ok(len(shorts) >= 2 and len(st_titles) == len(shorts) and all(
            u.get("status", {}).get("containsSyntheticMedia") is True for u in shorts),
           f"several Shorts per track, different titles, AI flag ({len(shorts)} Shorts)")
        outd = next((d for d in (tmp / "output").iterdir() if (d / "short2.mp4").exists()), None)
        ok(outd is not None and (outd / "hook1.png").exists(), "Short 2 rendered with motion + hook line")
        vis = json.loads((outd / "visuals.json").read_text(encoding="utf-8")) if outd else {}
        ok(bool(vis.get("art")) and (outd / "art_wide.png").exists() and (outd / "art_tall.png").exists(),
           "cover pictures generated for the track (picture generator)")
        bon = [u for u in ups if "#Shorts" not in u.get("snippet", {}).get("title", "")][1:]
        ok(len(bon) == 1 and bon[0]["status"].get("publishAt") and bon[0]["snippet"]["title"] != ups[0]["snippet"]["title"]
           and bon[0]["status"].get("containsSyntheticMedia") is True,
           f"second good variant -> separate scheduled video ({len(bon)})")
        main_v = ups[0]
        main_shorts = [u for u in shorts if "Selftest Horizon" in u["snippet"]["title"]]
        ok(main_shorts and all(u["status"].get("publishAt") == main_v["status"].get("publishAt") for u in main_shorts),
           "Shorts go live together with their video")
        if bon:
            b_shorts = [u for u in shorts if u not in main_shorts]
            from datetime import datetime as _dt
            d_main = _dt.strptime(main_v["status"]["publishAt"], "%Y-%m-%dT%H:%M:%SZ")
            d_bon = _dt.strptime(bon[0]["status"]["publishAt"], "%Y-%m-%dT%H:%M:%SZ")
            ok(b_shorts and all(u["status"].get("publishAt") == bon[0]["status"]["publishAt"] for u in b_shorts)
               and 20 <= (d_bon - d_main).total_seconds() / 3600 <= 28,
               f"second track + its Shorts scheduled for the next day ({len(b_shorts)} Shorts)")
        bp = run("--bench-image")
        ist = json.loads((tmp / "state" / "images.json").read_text(encoding="utf-8")) if (tmp / "state" / "images.json").exists() else {}
        ok(bp.returncode == 0 and ist.get("best_model") == "klein" and int(ist.get("best_threads") or 0) > 0,
           f"picture speed test picks model + threads ({ist.get('best_model')}, {ist.get('best_threads')})")
        rp = run("--report")
        rep_txt = next((tmp / "output").glob("report-*.txt"), None)
        hist2 = json.loads(hf.read_text(encoding="utf-8")) if hf.exists() else []
        ok(rp.returncode == 0 and rep_txt is not None and "SHORTY" in rep_txt.read_text(encoding="utf-8")
           and any(h.get("avg_view_pct") for h in hist2),
           "YouTube Analytics report (per video + style, CTR fallback)")

        # 4. running again the same day must NOT upload again
        before = len(CAPTURE["uploads"])
        p = run()
        ok(p.returncode == 0 and len(CAPTURE["uploads"]) == before, "second run on the same day does not re-upload")

        # 4b. wrong target channel in config -> refuse to upload (another date, so a new upload would happen)
        cfg_ok = (tmp / "config" / "config.yaml").read_text(encoding="utf-8")
        bad = json.loads(cfg_ok)
        bad.setdefault("channel", {})["expected_handle"] = "@someotherchannel"
        (tmp / "config" / "config.yaml").write_text(json.dumps(bad), encoding="utf-8")
        before = len(CAPTURE["uploads"])
        p = run("--date", "2031-01-07")
        ok(p.returncode != 0 and len(CAPTURE["uploads"]) == before and "WRONG CHANNEL" in (p.stdout + p.stderr),
           "wrong target channel -> nothing uploaded")
        (tmp / "config" / "config.yaml").write_text(cfg_ok, encoding="utf-8")
        chj = json.loads((tmp / "state" / "channel.json").read_text(encoding="utf-8")) if (tmp / "state" / "channel.json").exists() else {}
        ok(chj.get("id") == "UCselftest", "uploaded channel recorded in state/channel.json")

        # 4c. the old engine still works: Stable Audio 3 (sa3.cpp) in sections mode, dry run on another date
        sa3cfg = json.loads(cfg_ok)
        sa3cfg["music"]["engine"] = "sa3"
        (tmp / "config" / "config.yaml").write_text(json.dumps(sa3cfg), encoding="utf-8")
        p = run("--dry-run", "--date", "2031-02-03")
        b3 = tmp / "output" / "2031-02-03" / "brief.json"
        secs = len(json.loads(b3.read_text(encoding="utf-8")).get("sections", [])) if b3.exists() else 0
        ok(p.returncode == 0 and (tmp / "output" / "2031-02-03" / "track.wav").exists() and secs >= 2,
           f"sa3 engine still works (sections: {secs})" + ("" if p.returncode == 0 else f"\n{p.stdout[-1200:]}"))
        (tmp / "config" / "config.yaml").write_text(cfg_ok, encoding="utf-8")

        # 5. engine speed learned, model auto -> medium when its files exist, control panel API
        eng = json.loads((tmp / "state" / "engine.json").read_text(encoding="utf-8")) if (tmp / "state" / "engine.json").exists() else {}
        ok(bool(eng.get("sec_per_audio_sec")), "music engine speed measured (state/engine.json)")
        p = subprocess.run([sys.executable, "-c", PANEL_PROBE], env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, cwd=str(APP.parent))
        res = {}
        for line in p.stdout.splitlines():
            if line.startswith("PROBE "):
                res = json.loads(line[6:])
        ok(res.get("ace_auto") == "turbo", f"ACE-Step model auto -> turbo ({res.get('ace_auto')})")
        ok(res.get("auto_small") == "small-music" and res.get("auto_medium") == "medium",
           f"model auto: small without medium files, medium with them ({res.get('auto_small')}/{res.get('auto_medium')})"
           + ("" if res else f"\n{(p.stdout + p.stderr)[-1500:]}"))
        ok(res.get("status_ok") is True, "panel: status page data")
        ok(res.get("gui_import") is True, "panel window module loads (tkinter)" + ("" if res.get("gui_import") is True else f" {res.get('gui_import')}"))
        ok(res.get("saved") is True and res.get("bad_rejected") is True, "panel: settings saved, invalid values rejected" + ("" if res.get("saved") and res.get("bad_rejected") else f" {res}"))
        ok(res.get("disclosure_kept") is True, "panel: saving settings keeps the YouTube AI flag")
    return report(checks)


PANEL_PROBE = r'''
import json
from app.common import config
from app import panel
out = {}
p = config.paths(); cfg = config.load()
cfg["music"]["model"] = "auto"
out["ace_auto"] = config.resolve_model(cfg, p)
cfg["music"]["engine"] = "sa3"
out["auto_small"] = config.resolve_model(cfg, p)
md = p.sa3_dir / "models" / "medium"; md.mkdir(parents=True, exist_ok=True)
(md / "sa3-medium-dit-F32.gguf").write_bytes(b"x")
out["auto_medium"] = config.resolve_model(cfg, p)
(md / "sa3-medium-dit-F32.gguf").unlink()
st = panel.status()
out["status_ok"] = bool(st.get("styles")) and "settings" in st and "next_slot" in st
good = dict(st["settings"]); good.update(min_seconds=180, max_seconds=300, model="xl-turbo", variants=1, shorts_seconds=30)
try:
    panel.save_settings(good); c2 = config.load()
    out["saved"] = c2["music"]["max_seconds"] == 300 and c2["music"]["model"] == "xl-turbo"
except ValueError as e:
    out["saved"] = str(e)
try:
    panel.save_settings(dict(good, min_seconds=400, max_seconds=200)); out["bad_rejected"] = False
except ValueError:
    out["bad_rejected"] = True
out["disclosure_kept"] = config.load()["channel"]["contains_synthetic_media"] is True
try:
    import tkinter  # noqa: F401  (part of the Windows Python installer)
    import app.gui  # noqa: F401
    out["gui_import"] = True
except ImportError as e:
    out["gui_import"] = "tkinter" in str(e) or str(e)
print("PROBE " + json.dumps(out))
'''



def report(checks) -> int:
    print()
    for good, text in checks:
        print(f"  [{'OK  ' if good else 'FAIL'}] {text}")
    failed = sum(1 for g, _ in checks if not g)
    print(f"\n  Selftest: {len(checks) - failed}/{len(checks)} checks passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
