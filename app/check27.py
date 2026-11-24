"""Checks for the 2.7 features (no network, no engines, ~1 s).  Run:  .venv\\Scripts\\python.exe app\\check27.py
Kept separate from selftest.py on purpose (project rule: new features get their own test)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import seo, styles, variety  # noqa: E402
from app.steps import music  # noqa: E402
from app.steps import brief as B  # noqa: E402

results: list[tuple[bool, str]] = []


def ok(cond, what):
    results.append((bool(cond), what))


CFG = {"channel": {"title_suffix": "", "max_hashtags": 5}, "styles": {}, "music": {}}
HIST = [{"base_title": t, "key": "A minor", "bpm": 72, "mood": m, "style_family": "ambient", "substyle": "ambient drone",
         "title": f"Ambient Music for Sleep & Meditation – {t}", "scene": ""}
        for t, m in [("Morning Fog Haze", "calm"), ("Nightfall Echoes", "mysterious, eerie"), ("Dawn's Calm", "serene")] * 4]
rng = random.Random(7)
spec = styles.CATALOG["ambient"]["ambient drone"]
pick = {"family": "ambient", "substyle": "ambient drone", "spec": spec, "weight": 1, "share": 1, "why": {}, "top": []}

# key / tempo / mood avoid recent choices
fx = B.pick_fixed(pick, HIST, rng)
ok(fx["key"] != "A minor", f"key avoids the recent one ({fx['key']})")
ok("major" in fx["key"], "mode balanced after many minor tracks")
ok(fx["mood"] not in ("calm", "serene"), f"mood avoids recent ones ({fx['mood']})")
lo, hi = spec["bpm"]
ok(lo <= fx["bpm"] <= hi and abs(fx["bpm"] - 72) >= 3, f"tempo in range and away from recent ({fx['bpm']})")

# titles: cliches rejected, clean candidate kept
t = variety.choose_title(["Whispers of Twilight", "Echoes in the Mist", "Copper Kettle Morning"], HIST, rng)
ok(t == "Copper Kettle Morning", f"title without overused words chosen ({t})")
t2 = variety.choose_title(["Serene Echoes", "Nightfall Dreams"], HIST, rng)
ok(not (variety.words(t2) & variety.CLICHES), f"all-cliche ideas -> clean fallback title ({t2})")
ok(variety.choose_title(["My AI Song"], [], rng) != "My AI Song", "title with 'AI' rejected")

# brief normalize uses the fixed core and strips the LLM's own key/tempo/mood
raw = {"titles": ["Copper Kettle Morning"], "music_prompt": "ambient drone, A minor, 80 BPM, eerie, evolving pads",
       "description_story": "x", "hashtags": ["#ambient"]}
b = B.normalize(raw, pick, 280, CFG, HIST, rng, fx)
ok(b["key"] == fx["key"] and b["bpm"] == fx["bpm"] and b["mood"] == fx["mood"], "brief uses key/tempo/mood from code")
ok("A minor" not in b["music_prompt"] and "80 BPM" not in b["music_prompt"] and "eerie" not in b["music_prompt"],
   f"LLM key/tempo/foreign mood removed from prompt ({b['music_prompt']})")
ok(b["title"].endswith("– Copper Kettle Morning") and b["seo_phrase"] in seo.phrase_options("ambient drone", "ambient"),
   f"title = rotated search phrase + name ({b['title']})")

# alternative variant is a different piece; music.py maps variant 2 to it
alt = variety.alt_spec(b, spec, HIST, rng)
ok(alt["key"] != b["key"] and alt["mood"] != b["mood"], f"2nd variant: other key and mood ({alt['key']}, {alt['mood']})")
b["sections"] = [{"role": "full", "seconds": 280, "prompt": b["prompt_core"] + ", full track"}]
b["variants"] = [variety.main_spec(b), alt]
vb1, s1 = music._variant(b, b["sections"], 1)
vb2, s2 = music._variant(b, b["sections"], 2)
ok(vb1 is b and s1 == b["sections"], "variant 1 = main brief")
ok(vb2["key"] == alt["key"] and alt["prompt_core"] in s2[0]["prompt"], "variant 2 = own key + own prompt")
ok(B.variant_spec(b, "r0_v2.wav") == alt and B.variant_spec(b, "r1_v1.wav") == b["variants"][0], "file name -> variant spec")

# phrase rotation avoids the phrase used last time for this style
used = HIST[-1]["title"].split(" – ")[0]
ok(seo.pick_phrase("ambient drone", "ambient", HIST, random.Random(1)) != used, "search phrase rotates")

# 2.7.1: Shorts hooks/angles rotate and fit the genre; scenes/instruments per substyle; one drum part
s1 = seo.pick_shorts("cafe jazz", "acoustic", [], random.Random(1))
ok(len(s1["hooks"]) == 3 and len(set(s1["hooks"])) == 3 and len(s1["short_angles"]) == 2, "3 different hooks + 2 angles")
s2 = seo.pick_shorts("cafe jazz", "acoustic", [{"hooks": s1["hooks"], "short_angles": s1["short_angles"]}], random.Random(1))
ok(not set(s2["hooks"]) & set(s1["hooks"]), f"next track gets other hooks ({s2['hooks']})")
s3 = seo.pick_shorts("celtic", "acoustic", [], random.Random(2))
ok(not any("cafe" in a.lower() or "coffee" in a.lower() for a in s3["hooks"] + s3["short_angles"]),
   f"celtic Shorts without cafe lines ({s3['hooks']}, {s3['short_angles']})")
ok(seo.short_hook("acoustic", 1, {"hooks": ["A", "B", "C"]}) == "B", "Short uses the hooks of its brief")
sc = variety.pick_scene("acoustic", [], random.Random(3), "cafe jazz")
ok("cafe" in sc or "coffee" in sc or "espresso" in sc, f"cafe jazz scene is a cafe ({sc})")
cj = styles.CATALOG["acoustic"]["cafe jazz"]
a2 = variety.alt_spec({"style_family": "acoustic", "substyle": "cafe jazz", "key": "C major", "bpm": 100, "mood": "warm",
                       "instruments": cj["instruments"], "texture": ""}, cj, [], random.Random(4))
drum_parts = [i for i in a2["instruments"] if variety.words(i) & {"drums", "drum", "snare", "percussion"}]
ok(len(drum_parts) <= 1 and "fiddle" not in a2["instruments"], f"cafe jazz variant: jazz instruments, one drum part ({a2['instruments']})")

failed = [w for c, w in results if not c]
for c, w in results:
    print(f"  [{'OK  ' if c else 'FAIL'}] {w}")
print(f"\n  check27: {len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
