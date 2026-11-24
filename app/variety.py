"""Variety rules (2.7): fight repetition that the small local LLM produces on its own.

The local 7B model keeps choosing the same things (A minor, "serene/mysterious", titles with "Echoes",
"Twilight", "Serenity", cozy-cafe-at-twilight covers). Here the CODE decides the musical core of every
track (key, tempo, mood, scene direction) from larger pools while avoiding recent choices, and the LLM only
writes inside those limits. Titles: the LLM proposes several, the code picks the one least like the past.

Also builds the "alternative" spec for the second variant: different key, tempo, mood, lead instruments and
arrangement - so the bonus video is a genuinely different piece, not the same prompt with another seed.
Everything is deterministic for a given rng (seeded by date) and history.
"""
from __future__ import annotations

import random
import re

from . import styles

MOODS = {
    "electronic": ["dreamy", "driving", "nostalgic", "euphoric", "hypnotic", "brooding", "neon-lit", "propulsive",
                   "bittersweet", "glowing", "restless", "uplifting"],
    "chill": ["cozy", "mellow", "warm", "relaxed", "late-night", "sunny", "playful", "rainy", "hazy",
              "unhurried", "wistful", "content"],
    "classical": ["calm", "tender", "reflective", "graceful", "melancholic", "stately", "lyrical", "pastoral",
                  "hopeful", "intimate", "noble", "lively"],
    "cinematic": ["epic", "hopeful", "mysterious", "heroic", "bittersweet", "triumphant", "ominous", "adventurous",
                  "solemn", "awe-struck", "urgent", "tender"],
    "ambient": ["floating", "spacious", "meditative", "weightless", "glacial", "luminous", "drifting",
                "submerged", "nocturnal", "earthy", "crystalline", "slow-breathing"],
    "acoustic": ["sunny", "rustic", "heartfelt", "carefree", "wistful", "breezy", "homely", "golden",
                 "nimble", "earthy", "bright", "gentle"],
}

# arrangement / production angle - makes two pieces in one style sound different
TEXTURES = {
    "electronic": ["sparse arrangement with a single lead", "lush layered synths", "punchy rhythm section up front",
                   "slow build with long filter sweeps", "minimal groove with deep sub", "bright arpeggios and wide stereo"],
    "chill": ["sparse and intimate", "warm tape-saturated mix", "laid-back swing groove", "rain-soaked textures",
              "jazzy chord changes", "soft melodic hook"],
    "classical": ["intimate chamber sound", "flowing counterpoint", "slow singing melody", "light dance rhythm",
                  "rich resonant hall", "delicate staccato figures"],
    "cinematic": ["slow build to a big finale", "pulsing ostinato", "sparse and suspenseful", "soaring main theme",
                  "low percussive drive", "intimate solo instrument over strings"],
    "ambient": ["very slow evolving textures", "glassy high shimmer", "deep low drones", "gentle melodic fragments",
                "granular particles", "airy wide reverb"],
    "acoustic": ["fingerpicked and intimate", "lively strummed rhythm", "folk dance feel", "slow waltz feel",
                 "warm room sound", "bright melodic hook"],
}

# extra instruments per family (mixed with the substyle's own list for the alternative variant)
EXTRA_INSTRUMENTS = {
    "electronic": ["pluck synth", "vocoder-like pad", "electric piano", "modular bleeps", "warm analog bass", "glassy bells"],
    "chill": ["nylon guitar", "flute", "wurlitzer", "vibraphone", "soft synth pad", "muted guitar"],
    "classical": ["cello solo", "flute", "clarinet", "harp", "celesta", "oboe"],
    "cinematic": ["solo cello", "piano", "choir pad", "low brass", "frame drum", "celesta"],
    "ambient": ["soft piano", "bowed metal", "warm synth", "singing bowl tones", "felt piano", "low cello drone"],
    "acoustic": ["mandolin", "upright bass", "accordion", "fiddle", "ukulele", "brushed snare"],
}

# scene direction for the cover picture: setting x time x weather
SETTINGS = {
    "electronic": ["empty highway through a desert", "rooftop above a futuristic city", "train window at night",
                   "neon arcade street", "coastal road with wind turbines", "observatory dome", "parking deck on a hill"],
    "chill": ["small apartment with plants", "train compartment", "bookshop corner", "balcony with fairy lights",
              "laundromat at night", "bus stop in the suburbs", "attic room with a skylight"],
    "classical": ["old library", "conservatory greenhouse", "stone courtyard with a fountain", "music room with tall windows",
                  "lakeside pavilion", "monastery garden", "snowy palace terrace"],
    "cinematic": ["mountain pass", "ancient ruins in a jungle", "lighthouse on a cliff", "desert canyon",
                  "frozen sea with a ship", "floating sky city", "vast salt flat"],
    "ambient": ["mirror-like lake", "underwater kelp forest", "glacier cave", "moss forest", "starfield above dunes",
                "quiet harbor", "rice terraces in clouds"],
    "acoustic": ["wooden porch", "sunflower field", "fishing village", "cabin by a river", "vineyard hills",
                 "harbor cafe terrace", "orchard with a swing"],
}
# 2.7.1: where the family pool does not fit the genre (cafe jazz in "acoustic" got sunflower fields and a fiddle)
SETTINGS_SUB = {
    "cafe jazz": ["small corner cafe with a window seat", "jazz cafe with a piano and warm lamps", "street cafe terrace",
                  "bookshop cafe", "cafe counter with an espresso machine", "cafe by a canal", "rooftop cafe"],
    "smooth jazz": ["hotel lounge with a city view", "wine bar with soft lamps", "penthouse with a skyline view",
                    "harbor bar by the water", "jazz club stage with a saxophone stand"],
    "jazzhop": ["record shop", "small studio with a turntable", "cafe window seat", "city park bench"],
    "celtic": ["green hills with stone walls", "old stone bridge", "coastal cliffs", "village tavern",
               "standing stones", "harbor village"],
    "sci-fi score": ["spaceship bridge facing a planet", "alien desert under two moons", "orbital station window",
                     "futuristic city at night", "nebula seen from a small shuttle"],
    "space ambient": ["planet rings seen from orbit", "astronaut floating near a station", "nebula clouds",
                      "quiet moon surface with Earth rising", "deep space observatory"],
    "dark score": ["abandoned mansion hallway", "foggy forest path", "ruined chapel", "old lighthouse in a storm",
                   "empty train station"],
    "fantasy adventure": ["castle above a valley", "elven forest with lanterns", "dragon over the mountains",
                          "wizard tower library", "harbor of a fantasy city"],
}
EXTRA_SUB = {
    "cafe jazz": ["vibraphone", "muted trumpet", "clarinet", "jazz guitar", "brushed snare", "upright bass"],
    "smooth jazz": ["tenor saxophone-like lead", "flugelhorn-like lead", "jazz guitar", "rhodes piano"],
    "celtic": ["uilleann pipes-like lead", "bouzouki", "irish flute", "accordion"],
    "acoustic guitar": ["second acoustic guitar", "cello", "soft piano", "shaker"],
    "drum and bass": ["rhodes piano", "string pad", "vocal-like pad", "sub bass"],
    "baroque": ["oboe", "recorder", "harpsichord", "cello continuo"],
}
TIMES = ["dawn", "early morning", "late morning", "noon", "golden hour", "blue hour", "deep night", "overcast afternoon"]
WEATHER = ["clear sky", "light rain", "fresh snow", "drifting fog", "after the rain", "warm wind", "first frost", "summer haze"]

# words the 7B model overuses in titles - always avoided (on top of words from recent titles)
CLICHES = {"echo", "echoes", "whisper", "whispers", "whispering", "serenity", "serene", "serenade", "dream", "dreams",
           "dreamscape", "twilight", "nightfall", "midnight", "moonlit", "mist", "mists", "misty", "shadow", "shadows",
           "ethereal", "reverie", "lullaby", "haze", "tranquil", "tranquility", "revisited", "veil", "eternal", "embrace"}
STOP = {"the", "a", "an", "of", "in", "on", "at", "and", "to", "for", "by", "with", "from", "ii", "iii", "my", "our", "your"}

# fallback title parts (no cliches)
TITLE_A = ["Copper", "Velvet", "Silver", "Paper", "Glass", "Amber", "Cobalt", "Northern", "Quiet", "Distant", "Faded",
           "Electric", "Crimson", "Wandering", "Saffron", "Hollow", "Slow", "Little", "Lantern", "Harbor", "Juniper",
           "Marble", "Indigo", "Pale", "Open", "Low", "Late", "Wild", "Coral", "Linen"]
TITLE_B = ["Horizon", "Lanterns", "Drift", "Harbor", "Skyline", "Letters", "Garden", "Signal", "Tides", "Embers",
           "Voyage", "Avenue", "Bloom", "Meridian", "Compass", "Orbit", "Path", "Atlas", "Windows", "Rooftops",
           "Postcards", "Currents", "Fields", "Kites", "Satellites", "Trains", "Glasshouse", "Islands", "Pages", "Steps"]


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", str(text or "").lower()) if len(w) >= 3 and w not in STOP}


def banned_title_words(history: list, last: int = 15) -> set[str]:
    """Cliches + every word of the last `last` titles (older titles only count as overlap, not as a ban)."""
    used: set[str] = set()
    for h in history[-last:]:
        used |= words(h.get("base_title") or "")
    return used | CLICHES


# mood words the LLM likes to add on its own - removed from the music prompt when they are not today's mood
LLM_MOODS = {"eerie", "mysterious", "serene", "tranquil", "ethereal", "haunting", "melancholic", "calm", "reflective",
             "atmospheric", "moody", "dark", "relaxed", "peaceful", "dreamy", "nostalgic", "suspenseful", "enigmatic",
             "mellow", "soothing", "evocative", "mystical"}


def clean_prompt_moods(prompt: str, mood: str) -> str:
    keep = words(mood)
    all_moods = LLM_MOODS | {w for ms in MOODS.values() for m in ms for w in words(m)}
    parts = []
    for p in [x.strip() for x in str(prompt).split(",")]:
        pw = words(p)
        if p and pw and pw <= all_moods and not (pw & keep):
            continue  # a pure mood token that is not today's mood
        if p:
            parts.append(p)
    return ", ".join(parts)


def _recent(history: list, field: str, n: int) -> list:
    return [h.get(field) for h in history[-n:] if h.get(field)]


def pick_key(history: list, rng: random.Random, avoid: list[str] | None = None) -> str:
    """Key not used in the last 8 tracks (and not in `avoid`); balanced major/minor."""
    recent = {str(k).lower() for k in _recent(history, "key", 8)} | {str(k).lower() for k in (avoid or [])}
    pool = [k for k in styles.KEYS if k.lower() not in recent] or list(styles.KEYS)
    # prefer the mode used less often lately (the LLM drifted to minor almost every time)
    minors = sum(1 for k in _recent(history, "key", 10) if "minor" in str(k).lower())
    majors = len(_recent(history, "key", 10)) - minors
    want = "major" if minors > majors else "minor" if majors > minors else rng.choice(["major", "minor"])
    pref = [k for k in pool if want in k] or pool
    return rng.choice(pref)


def pick_bpm(spec: dict, history: list, rng: random.Random, family: str, avoid: list[int] | None = None) -> int:
    lo, hi = spec.get("bpm", (70, 110))
    gap = max(3, (hi - lo) // 4)
    near = [int(b) for b in _recent([h for h in history if h.get("style_family") == family], "bpm", 3)] + list(avoid or [])
    options = [b for b in range(lo, hi + 1) if all(abs(b - x) >= gap for x in near)]
    return rng.choice(options or list(range(lo, hi + 1)))


def pick_mood(family: str, history: list, rng: random.Random, avoid: list[str] | None = None) -> str:
    recent = set()
    for m in _recent(history, "mood", 10) + list(avoid or []):
        recent |= words(m)
    pool = MOODS.get(family) or MOODS["chill"]
    fresh = [m for m in pool if not (words(m) & recent)]
    return rng.choice(fresh or pool)


def pick_texture(family: str, rng: random.Random, avoid: str = "") -> str:
    pool = [t for t in (TEXTURES.get(family) or TEXTURES["chill"]) if t != avoid]
    return rng.choice(pool)


def pick_scene(family: str, history: list, rng: random.Random, substyle: str | None = None) -> str:
    recent = " ".join(str(h.get("scene") or "") for h in history[-8:]).lower()
    pool = SETTINGS_SUB.get(str(substyle or "").lower()) or SETTINGS.get(family) or SETTINGS["chill"]
    settings = [s for s in pool if s not in recent] or pool
    times = [t for t in TIMES if t not in recent] or TIMES
    weather = [w for w in WEATHER if w not in recent] or WEATHER
    return f"{rng.choice(settings)}, {rng.choice(times)}, {rng.choice(weather)}"


def title_score(t: str, history: list, banned: set[str]) -> tuple[int, int, float]:
    """(cliche words, words from recent titles, max word-overlap with any past title) - lower is better."""
    w = words(t)
    overlap = 0.0
    for h in history:
        hw = words(h.get("base_title") or "")
        if w and hw:
            overlap = max(overlap, len(w & hw) / len(w | hw))
    return len(w & CLICHES), len(w & (banned - CLICHES)), overlap


def choose_title(candidates: list, history: list, rng: random.Random, extra_used: set[str] | None = None) -> str:
    """Pick the LLM idea with no cliche, at most one word shared with recent titles and the least overlap with past
    titles; when every idea fails, build a name from the clean word pools."""
    banned = banned_title_words(history) | {w for t in (extra_used or set()) for w in words(t)}
    used = {str(h.get("base_title", "")).lower() for h in history} | {t.lower() for t in (extra_used or set())}
    clean = []
    for c in candidates:
        c = re.sub(r"\s+", " ", str(c or "")).strip().strip('"').strip("'")
        c = re.sub(r"\s*[|\-–]\s*AI.*$", "", c, flags=re.I).strip()
        if 3 <= len(c) <= 50 and c.lower() not in used and not re.search(r"\bAI\b", c):
            clean.append(c)
    scored = sorted(clean, key=lambda c: title_score(c, history, banned))
    if scored:
        cl, recent, overlap = title_score(scored[0], history, banned)
        if cl == 0 and recent <= 1 and overlap < 0.5:
            return scored[0]
    for _ in range(60):
        a, b = rng.choice(TITLE_A), rng.choice(TITLE_B)
        t = f"{a} {b}"
        if not (words(t) & banned) and t.lower() not in used:
            return t
    return scored[0] if scored else f"{rng.choice(TITLE_A)} {rng.choice(TITLE_B)}"


def core(music_prompt: str, key: str, bpm: int) -> str:
    return f"{music_prompt}, {key}, {bpm} BPM, instrumental, no vocals, high quality"


def main_spec(b: dict) -> dict:
    return {k: b.get(k) for k in ("key", "bpm", "mood", "instruments", "music_prompt", "prompt_core", "texture")}


def alt_spec(b: dict, spec: dict, history: list, rng: random.Random) -> dict:
    """Second variant: same style, clearly different piece (key, tempo, mood, lead instruments, arrangement)."""
    family, sub = b["style_family"], b["substyle"]
    key = pick_key(history, rng, avoid=[b["key"]])
    bpm = pick_bpm(spec, history, rng, family, avoid=[int(b["bpm"])])
    mood = pick_mood(family, history, rng, avoid=[b["mood"]])
    own = list(spec.get("instruments") or [])
    pool = EXTRA_SUB.get(str(sub).lower()) or EXTRA_INSTRUMENTS.get(family) or []
    extra = [i for i in pool if i not in (b.get("instruments") or [])]
    rng.shuffle(own)
    rng.shuffle(extra)
    lead = extra[:1] or own[:1]
    inst = []
    drums = {"drums", "drum", "snare", "percussion", "kick", "breakbeat", "bodhran", "shaker"}
    for i in lead + own + extra[1:]:  # max one drum part ("brushed drums" + "brushed snare" was a duplicate)
        if i in inst or (words(i) & drums and any(words(x) & drums for x in inst)):
            continue
        inst.append(i)
    inst = inst[:4]
    texture = pick_texture(family, rng, avoid=b.get("texture", ""))
    mp = f"{sub}, {mood}, {', '.join(inst)}, {texture}"[:260]
    return {"key": key, "bpm": bpm, "mood": mood, "instruments": inst, "music_prompt": mp,
            "prompt_core": core(mp, key, bpm), "texture": texture}
