"""Step 2: creative brief.

The STYLE is chosen in code from research (app/styles.py) - the local LLM only writes the creative
details for that style (title, mood, prompt, story, tags). Everything is validated and repaired in code;
if the LLM is unavailable a deterministic fallback brief is used, so the run never stops here.
"""
from __future__ import annotations

import random
import re

from .. import styles
from ..common import llm
from ..common.config import APP_DIR
from ..common.util import Ctx, engine_cap, file_ok, load_history, read_json, write_json

REQUIRED = ["music_prompt", "description_story", "hashtags"]  # titles/title checked in normalize()
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

PALETTES = {
    "electronic": [["#12002B", "#FF2E88", "#22D3EE"], ["#0B0C2A", "#7C3AED", "#F472B6"], ["#03111F", "#00B4D8", "#F9C74F"]],
    "chill": [["#2B2D42", "#F4A261", "#E9C46A"], ["#1D3557", "#A8DADC", "#F1FAEE"], ["#3D2C2E", "#C9ADA7", "#F2E9E4"]],
    "classical": [["#1F2937", "#D6C7A1", "#7C9A92"], ["#2E1F27", "#C5A880", "#F3E9DC"], ["#0F172A", "#94A3B8", "#E2E8F0"]],
    "cinematic": [["#0B1320", "#C2410C", "#94A3B8"], ["#111111", "#B91C1C", "#F59E0B"], ["#0A1931", "#185ADB", "#FFC947"]],
    "ambient": [["#0B132B", "#3A506B", "#5BC0BE"], ["#10002B", "#5A189A", "#C77DFF"], ["#0D1B2A", "#415A77", "#E0E1DD"]],
    "acoustic": [["#3E2723", "#A1887F", "#FFE0B2"], ["#1B4332", "#74C69D", "#D8F3DC"], ["#283618", "#DDA15E", "#FEFAE0"]],
}
MOODS = {
    "electronic": ["dreamy", "driving", "nostalgic", "euphoric", "hypnotic"],
    "chill": ["cozy", "mellow", "warm", "relaxed", "late-night"],
    "classical": ["calm", "tender", "reflective", "graceful", "melancholic"],
    "cinematic": ["epic", "hopeful", "mysterious", "heroic", "bittersweet"],
    "ambient": ["serene", "floating", "spacious", "meditative", "weightless"],
    "acoustic": ["sunny", "rustic", "heartfelt", "carefree", "wistful"],
}
TITLE_WORDS = (["Neon", "Velvet", "Silver", "Golden", "Hidden", "Northern", "Quiet", "Distant", "Paper", "Glass",
                "Amber", "Midnight", "Morning", "Winter", "Ocean", "Faded", "Electric", "Crimson", "Wandering", "Last"],
               ["Horizon", "Lanterns", "Drift", "Echoes", "Harbor", "Skyline", "Letters", "Garden", "Signal", "Tides",
                "Embers", "Voyage", "Reverie", "Avenue", "Bloom", "Meridian", "Hollow", "Compass", "Orbit", "Path"])
SECTION_HINTS = {
    "intro": "gentle intro that introduces the main theme",
    "main": "main section with the full theme",
    "variation": "variation of the theme with a new melodic idea and more energy",
    "breakdown": "breakdown, stripped-back and atmospheric, same key and tempo",
    "climax": "climax, fullest arrangement, emotional peak",
    "outro": "outro, the theme returns softly and fades",
}


def _performance(history: list) -> str:
    per: dict[str, list[int]] = {}
    for h in history:
        if h.get("views") is not None:
            per.setdefault(h.get("substyle", "?"), []).append(int(h["views"]))
    if not per:
        return "no data yet"
    return "; ".join(f"{k}: avg {sum(v) // len(v)} views ({len(v)} videos)" for k, v in per.items())


# fallback scenes for the cover picture (when the language model gives no image_prompt)
SCENES = {
    "chill": ["young person with big headphones studying at a desk by a rainy window at night, warm desk lamp, city lights",
              "cozy bedroom at dusk, cat sleeping on a desk next to a notebook and a cup of tea, soft neon light"],
    "electronic": ["lone driver in a retro car on an empty neon highway at night, city skyline, wet asphalt reflections",
                   "person with headphones on a rooftop above a futuristic city at night, glowing signs, light rain"],
    "classical": ["grand piano in a sunlit old library, dust in the light beams, tall windows, quiet morning",
                  "candle-lit study room with old books and a violin on a velvet chair, warm evening light"],
    "cinematic": ["lone wanderer on a cliff above a vast misty valley at sunrise, epic scale, dramatic clouds",
                  "astronaut looking at a giant planet through a spaceship window, deep space, cinematic light"],
    "ambient": ["calm lake at night under a sky full of stars and aurora, mirror reflections, mist",
                "floating islands above clouds at twilight, soft glow, dreamy and peaceful"],
    "acoustic": ["cozy coffee shop on a rainy afternoon, steaming cup by the window, warm bokeh lights",
                 "acoustic guitar on a wooden porch at golden hour, fields and soft sunlight"],
}


def _clean_text(s, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip("'")
    return s[:limit].rstrip()


def pick_fixed(pick: dict, history: list, rng: random.Random) -> dict:
    """2.7: the musical core and the scene direction are decided in code (app/variety.py), not by the LLM."""
    from .. import variety
    family = pick["family"]
    return {"key": variety.pick_key(history, rng), "bpm": variety.pick_bpm(pick["spec"], history, rng, family),
            "mood": variety.pick_mood(family, history, rng), "texture": variety.pick_texture(family, rng),
            "scene": variety.pick_scene(family, history, rng, pick["substyle"])}


def normalize(raw: dict, pick: dict, length: int, cfg: dict, history: list, rng: random.Random,
              fixed: dict | None = None) -> dict:
    from .. import variety
    family, sub, spec = pick["family"], pick["substyle"], pick["spec"]
    fixed = fixed or pick_fixed(pick, history, rng)
    b: dict = {"style_family": family, "substyle": sub, "target_seconds": length,
               "style_pick": {k: pick[k] for k in ("weight", "share", "why", "top")}}
    lo, hi = spec.get("bpm", (70, 110))
    b["mood"] = fixed["mood"]
    b["bpm"] = max(lo, min(hi, int(fixed["bpm"])))
    b["key"] = fixed["key"]
    b["texture"] = fixed["texture"]
    b["scene"] = fixed["scene"]
    inst = raw.get("instruments")
    if isinstance(inst, str):
        inst = [i.strip() for i in inst.split(",")]
    b["instruments"] = [_clean_text(i, 30) for i in (inst or spec.get("instruments") or []) if str(i).strip()][:5]

    mp = _clean_text(raw.get("music_prompt"), 260)
    if len(mp) < 20:
        mp = f"{sub}, {b['mood']}, {', '.join(b['instruments'])}"
    # the LLM must not set key/tempo (they are fixed) - drop any it wrote anyway
    mp = re.sub(r",?\s*\b\d{2,3}\s*BPM\b", "", mp, flags=re.I)
    mp = re.sub(r",?\s*\b[A-G]( sharp| flat|#|b)? (major|minor)\b", "", mp, flags=re.I).strip(" ,")
    mp = variety.clean_prompt_moods(mp, b["mood"])  # the LLM's own "eerie, mysterious" would fight the fixed mood
    if sub.lower() not in mp.lower():
        mp = f"{sub}, {mp}"
    if b["mood"].lower() not in mp.lower():
        mp = f"{mp}, {b['mood']}"
    if b["texture"].lower() not in mp.lower():
        mp = f"{mp}, {b['texture']}"
    b["music_prompt"] = mp[:260]
    # the same core (style + key + tempo) goes into every section, so sections fit together
    b["prompt_core"] = variety.core(b["music_prompt"], b["key"], b["bpm"])

    vt = raw.get("visual_theme") if isinstance(raw.get("visual_theme"), dict) else {}
    pal = [c.strip().upper() for c in (vt.get("palette") or []) if isinstance(c, str) and HEX.match(c.strip())]
    b["visual_theme"] = {"palette": pal[:3] if len(pal) >= 3 else rng.choice(PALETTES.get(family, PALETTES["electronic"])),
                         "motif": _clean_text(vt.get("motif"), 40) or b["mood"]}

    suffix = cfg["channel"]["title_suffix"]
    cands = raw.get("titles") if isinstance(raw.get("titles"), list) else []
    cands = [*cands, raw.get("title")]
    # 2.7: several LLM ideas -> the one without overused words and least like past titles (else clean word pools)
    t = variety.choose_title([c for c in cands if c], history, rng)
    b["base_title"] = t
    b["title_candidates"] = [str(c)[:60] for c in cands if c][:8]
    # search phrase first: "Chillstep Music to Relax & Study – Copper Kettle Morning"; 2.7: the phrase rotates
    from .. import seo
    b["seo_phrase"] = seo.pick_phrase(sub, family, history, rng)
    b["title"] = seo.title(sub, t, suffix, lead=b["seo_phrase"])[:100]

    ip = _clean_text(raw.get("image_prompt"), 400)
    ip = re.sub(r"\b(text|letters|logo|watermark|signature)\b", "", ip, flags=re.I)
    b["image_prompt"] = ip if len(ip) > 25 else f"{b['scene']}, {b['mood']} atmosphere, {b['visual_theme']['motif']}"

    b["description_story"] = _clean_text(raw.get("description_story"), 600) or (
        f"A {b['mood']} {sub} piece in {b['key']} at {b['bpm']} BPM - made for focus, relaxing and long listening sessions.")
    tags = raw.get("hashtags") or []
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    clean: list[str] = []
    from .. import seo
    # the first 3 hashtags appear above the title on YouTube -> the strongest search words of this style
    for tg in [*seo.first_hashtags(sub), "#" + re.sub(r"[^A-Za-z0-9]", "", sub.title()), *tags]:
        tg = "#" + re.sub(r"[^A-Za-z0-9_]", "", str(tg))
        if len(tg) > 2 and tg.lower() not in [c.lower() for c in clean]:
            clean.append(tg)
    if len(clean) < 3:
        clean.append("#Instrumental")
    b["hashtags"] = clean[: int(cfg["channel"]["max_hashtags"])]
    d = pick.get("why", {}).get("demand")
    b["trend_reason"] = _clean_text(raw.get("trend_reason"), 200) or (
        f"{sub.title()} is currently in demand on YouTube." if d else f"Exploring {sub} to keep the channel varied.")
    return b


def plan_sections(b: dict, cfg: dict, rng: random.Random, learned_cap: int | None = None) -> list[dict]:
    """Split the target length into sections the engine can generate (each <= section_max_seconds)."""
    mc = cfg["music"]
    target, cap, xf = int(b["target_seconds"]), int(mc["section_max_seconds"]), float(mc["crossfade_seconds"])
    native_cap = int((mc.get("native_max_seconds") or {}).get(mc["model"], 120))
    if learned_cap:  # the engine proved it cannot do more than this in one go
        native_cap = min(native_cap, learned_cap)
        cap = max(10, min(cap, learned_cap - 1))
    mode = mc.get("mode", "auto")
    if mode == "native" or (mode == "auto" and target <= native_cap):
        return [{"role": "full", "seconds": target, "prompt": f"{b['prompt_core']}, full track with intro, development, climax and outro"}]
    n = max(2, -(-int(target + xf) // cap))  # ceil((target + overlaps) / cap)
    while (target + xf * (n - 1)) / n > cap:
        n += 1
    roles = {2: ["main", "outro"], 3: ["intro", "variation", "outro"], 4: ["intro", "main", "climax", "outro"],
             5: ["intro", "main", "breakdown", "climax", "outro"]}.get(n) or (["intro"] + ["main", "variation"] * n)[: n - 1] + ["outro"]
    total = target + xf * (n - 1)
    # slightly uneven section lengths sound more natural than equal blocks
    weights = [rng.uniform(0.85, 1.15) for _ in roles]
    secs = [total * w / sum(weights) for w in weights]
    secs = [min(cap, max(20, round(s))) for s in secs]
    secs[-1] += round(total - sum(secs))
    secs[-1] = min(cap, max(20, secs[-1]))
    return [{"role": r, "seconds": int(s), "prompt": f"{b['prompt_core']}, {SECTION_HINTS.get(r, r)}"} for r, s in zip(roles, secs)]


def run(ctx: Ctx, trends: dict) -> dict:
    out = ctx.out_dir / "brief.json"
    if file_ok(out):
        b = read_json(out)
        if b and b.get("sections"):
            return b
    from .. import variety
    history = load_history(ctx.paths)
    rng = random.Random(ctx.date.toordinal())
    pick = styles.choose(ctx.cfg, trends, history, rng, ctx.log)
    length = styles.target_length(ctx.cfg, pick["spec"], rng)
    fixed = pick_fixed(pick, history, rng)
    recent_txt = "\n".join(
        f"- {h.get('base_title', h.get('title'))} | {h.get('substyle')} | {h.get('mood')} | {h.get('key')} | {h.get('bpm')} BPM"
        for h in history[-10:]
    ) or "- none yet"
    vc = ctx.cfg.get("variety") or {}
    prompt = (APP_DIR / "prompts" / "brief.txt").read_text(encoding="utf-8").format(
        family=pick["family"], substyle=pick["substyle"], key=fixed["key"], bpm=fixed["bpm"], mood=fixed["mood"],
        texture=fixed["texture"], scene=fixed["scene"],
        instruments=", ".join(pick["spec"].get("instruments", [])) or "free choice",
        minutes=f"{length // 60}:{length % 60:02d}", performance=_performance(history), recent_tracks=recent_txt,
        banned=", ".join(sorted(variety.banned_title_words(history))), n_titles=int(vc.get("title_candidates", 5)),
    )
    raw = llm.ask_json(ctx.cfg, prompt, REQUIRED, ctx.log) if llm.server_up(ctx.cfg) else None
    if raw is None:
        ctx.note("LLM unavailable or invalid - using the built-in fallback brief.")
        raw = {}
    brief = normalize(raw, pick, length, ctx.cfg, history, rng, fixed)
    brief["sections"] = plan_sections(brief, ctx.cfg, rng, engine_cap(ctx.paths, str(ctx.cfg["music"]["model"])))
    brief.update(seo_shorts(brief, history, rng))
    if vc.get("distinct_variants", True) and int(ctx.cfg["music"].get("variants", 2)) >= 2:
        # 2nd variant = a different piece in the same style (bonus video is not a copy of the main one)
        brief["variants"] = [variety.main_spec(brief), variety.alt_spec(brief, pick["spec"], history, rng)]
    brief["llm_fallback"] = not bool(raw)
    write_json(out, brief)
    ctx.log.info("Brief: %s | %s/%s | %s | %d BPM | %s | %s | target %d:%02d in %d section(s)", brief["title"],
                 brief["style_family"], brief["substyle"], brief["mood"], brief["bpm"], brief["key"], brief["texture"],
                 length // 60, length % 60, len(brief["sections"]))
    if brief.get("variants"):
        a = brief["variants"][1]
        ctx.log.info("Second variant (own piece): %s | %d BPM | %s | %s", a["mood"], a["bpm"], a["key"], ", ".join(a["instruments"]))
    if raw:
        ctx.log.info("Title ideas: %s -> chosen '%s'", " / ".join(brief["title_candidates"]), brief["base_title"])
    return brief


def seo_shorts(b: dict, history: list, rng: random.Random, avoid: list | None = None) -> dict:
    """2.7.1: hooks (on-screen line) + title angles of this track's Shorts, rotated against recent tracks."""
    from .. import seo
    return seo.pick_shorts(b["substyle"], b["style_family"], history, rng, avoid)


def variant_index(name: str | None) -> int | None:
    """'r0_v2.wav' -> 2"""
    m = re.search(r"_v(\d+)", str(name or ""))
    return int(m.group(1)) if m else None


def variant_spec(brief: dict, name: str | None) -> dict | None:
    """Music spec (key, bpm, mood, instruments, prompt) of the variant file `name`, or None (old brief / 1 variant)."""
    specs = brief.get("variants") or []
    i = variant_index(name)
    if len(specs) < 2 or not i:
        return None
    return specs[(i - 1) % len(specs)]


def apply_variant(ctx: Ctx, brief: dict, name: str | None) -> dict:
    """After the quality gate: when the alternative variant won, the main video describes THAT piece."""
    s = variant_spec(brief, name)
    if not s or all(brief.get(k) == v for k, v in s.items()):
        return brief
    brief.update(s)
    brief["chosen_variant"] = variant_index(name)
    write_json(ctx.out_dir / "brief.json", brief)
    ctx.log.info("Winner is variant %s -> video uses its key/tempo/mood (%s, %d BPM, %s)",
                 brief["chosen_variant"], brief["key"], brief["bpm"], brief["mood"])
    return brief
