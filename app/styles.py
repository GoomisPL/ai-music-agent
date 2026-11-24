"""Style catalog and research-driven style selection.

Every run picks ONE substyle. The choice combines:
  - demand      - how well the style performs on YouTube right now (trends.json, search research),
  - own results - average views of our own videos in that style (history.json),
  - exploration - styles we have never / rarely tried get a bonus,
  - variety     - hard rules: never the same substyle within the last `no_repeat_substyle` premieres,
                  never the same family more than `max_family_streak` times in a row,
                  and a soft penalty for recently used styles.
Final pick = weighted random draw (seeded by date), so the channel does not get stuck on one style.
"""
from __future__ import annotations

import math
import random

# family -> substyle -> settings. bpm = (min, max), length = preferred track length in seconds (min, max),
# query = what we search on YouTube to measure demand, sections = arrangement roles for long tracks.
CATALOG: dict[str, dict[str, dict]] = {
    "electronic": {
        "synthwave":        {"bpm": (85, 118), "length": (200, 300), "query": "synthwave instrumental",
                             "instruments": ["analog synth", "gated drums", "bass synth", "arpeggiator"]},
        "deep house":       {"bpm": (118, 124), "length": (240, 320), "query": "deep house instrumental mix",
                             "instruments": ["four-on-the-floor kick", "warm bass", "chord stabs", "pads"]},
        "drum and bass":    {"bpm": (165, 175), "length": (200, 280), "query": "liquid drum and bass instrumental",
                             "instruments": ["breakbeat drums", "reese bass", "atmospheric pads", "piano"]},
        "downtempo":        {"bpm": (80, 100), "length": (200, 300), "query": "downtempo chill electronic",
                             "instruments": ["soft drums", "sub bass", "textured pads", "plucks"]},
        "melodic techno":   {"bpm": (120, 126), "length": (260, 320), "query": "melodic techno instrumental",
                             "instruments": ["driving kick", "rolling bass", "arpeggiated synth", "atmospheres"]},
    },
    "chill": {
        "lofi hip hop":     {"bpm": (70, 90), "length": (170, 220), "query": "lofi hip hop beats to study",
                             "instruments": ["dusty drums", "rhodes piano", "vinyl crackle", "upright bass"]},
        "jazzhop":          {"bpm": (75, 95), "length": (170, 230), "query": "jazz hop instrumental",
                             "instruments": ["brushed drums", "jazz guitar", "electric piano", "muted trumpet pad"]},
        "chillstep":        {"bpm": (130, 145), "length": (220, 300), "query": "chillstep music",
                             "instruments": ["half-time drums", "wobble bass", "ethereal pads", "piano"]},
        "smooth jazz":      {"bpm": (80, 105), "length": (200, 280), "query": "smooth jazz instrumental",
                             "instruments": ["saxophone-like lead synth", "electric piano", "fretless bass", "light drums"]},
    },
    "classical": {
        "solo piano":       {"bpm": (60, 90), "length": (170, 260), "query": "relaxing solo piano music",
                             "instruments": ["grand piano"]},
        "string quartet":   {"bpm": (60, 100), "length": (200, 300), "query": "string quartet instrumental",
                             "instruments": ["violin", "viola", "cello"]},
        "baroque":          {"bpm": (80, 120), "length": (170, 260), "query": "baroque music instrumental",
                             "instruments": ["harpsichord", "strings", "continuo"]},
        "minimalist piano": {"bpm": (60, 85), "length": (200, 300), "query": "minimalist piano music",
                             "instruments": ["piano", "soft strings"]},
    },
    "cinematic": {
        "epic orchestral":  {"bpm": (80, 120), "length": (180, 260), "query": "epic orchestral music",
                             "instruments": ["full orchestra", "taiko drums", "brass", "choir pad"]},
        "fantasy adventure": {"bpm": (85, 120), "length": (200, 280), "query": "fantasy adventure music",
                              "instruments": ["strings", "flute", "harp", "french horns"]},
        "dark score":       {"bpm": (60, 90), "length": (200, 300), "query": "dark cinematic music",
                             "instruments": ["low strings", "drones", "percussion", "piano"]},
        "emotional score":  {"bpm": (60, 85), "length": (180, 260), "query": "emotional cinematic piano",
                             "instruments": ["piano", "strings", "soft pads"]},
        "sci-fi score":     {"bpm": (80, 110), "length": (200, 300), "query": "sci-fi ambient soundtrack",
                             "instruments": ["synth pads", "pulsing bass", "orchestral hits", "textures"]},
    },
    "ambient": {
        "ambient drone":    {"bpm": (60, 80), "length": (260, 320), "query": "ambient music for sleep",
                             "instruments": ["evolving pads", "drones", "field textures"]},
        "space ambient":    {"bpm": (60, 80), "length": (260, 320), "query": "space ambient music",
                             "instruments": ["shimmer pads", "deep drones", "soft bells"]},
        "nature ambient":   {"bpm": (60, 80), "length": (240, 320), "query": "relaxing ambient nature music",
                             "instruments": ["soft pads", "kalimba", "flute", "water-like textures"]},
    },
    "acoustic": {
        "acoustic guitar":  {"bpm": (70, 110), "length": (170, 260), "query": "acoustic guitar instrumental",
                             "instruments": ["acoustic guitar", "soft percussion", "bass"]},
        "celtic":           {"bpm": (80, 120), "length": (180, 260), "query": "celtic music instrumental",
                             "instruments": ["tin whistle", "harp", "fiddle", "bodhran"]},
        "cafe jazz":        {"bpm": (90, 130), "length": (180, 260), "query": "cafe jazz instrumental",
                             "instruments": ["piano trio", "double bass", "brushed drums"]},
    },
}

KEYS = ["C major", "G major", "D major", "A major", "F major", "B flat major", "E flat major",
        "A minor", "E minor", "D minor", "B minor", "F sharp minor", "C minor", "G minor"]


def catalog(cfg: dict) -> dict[str, dict[str, dict]]:
    """Built-in catalog, optionally extended or reduced from config (styles.catalog / styles.exclude)."""
    cat = {f: {s: dict(v) for s, v in subs.items()} for f, subs in CATALOG.items()}
    for fam, subs in (cfg.get("styles", {}).get("catalog") or {}).items():
        for name, spec in (subs or {}).items():
            spec = dict(spec or {})
            for k in ("bpm", "length"):
                if k in spec:
                    spec[k] = tuple(spec[k])
            base = cat.setdefault(fam, {}).get(name, {"bpm": (70, 110), "length": (200, 280),
                                                       "query": f"{name} instrumental", "instruments": []})
            base.update(spec)
            cat[fam][name] = base
    for item in cfg.get("styles", {}).get("exclude") or []:
        item = str(item).lower()
        if item in cat:
            del cat[item]
        for fam in cat.values():
            fam.pop(item, None)
    return {f: s for f, s in cat.items() if s}


def all_styles(cfg: dict) -> list[tuple[str, str, dict]]:
    return [(fam, sub, spec) for fam, subs in catalog(cfg).items() for sub, spec in subs.items()]


def choose(cfg: dict, trends: dict, history: list, rng: random.Random, log=None) -> dict:
    sc = cfg.get("styles", {})
    no_repeat = int(sc.get("no_repeat_substyle", 6))
    max_streak = int(sc.get("max_family_streak", 1))
    explore_w = float(sc.get("exploration_weight", 0.35))
    demand = (trends or {}).get("substyle_demand", {}) or {}
    niche = {str(k).lower(): v for k, v in (sc.get("niche_boost") or {}).items()}
    recent = [h for h in history if h.get("substyle")]
    recent_subs = [h["substyle"] for h in recent[-no_repeat:]] if no_repeat else []
    last_fams = [h.get("style_family") for h in recent[-max_streak:]] if max_streak else []
    blocked_fam = last_fams[0] if max_streak and len(last_fams) == max_streak and len(set(last_fams)) == 1 else None

    # own performance per substyle (average views), shrunk towards the channel average
    views: dict[str, list[int]] = {}
    for h in recent:
        if h.get("views") is not None:
            views.setdefault(h["substyle"], []).append(int(h["views"]))
    all_v = [v for vs in views.values() for v in vs]
    pcts: dict[str, list[float]] = {}
    for h in recent:
        if h.get("avg_view_pct") is not None:
            pcts.setdefault(h["substyle"], []).append(float(h["avg_view_pct"]))
    all_p = [p for ps in pcts.values() for p in ps]
    chan_pct = (sum(all_p) / len(all_p)) if all_p else 0.0
    chan_avg = (sum(all_v) / len(all_v)) if all_v else 0.0

    # one viral video can make a style look 10x bigger than the rest -> cap at 1.5x the second-best style
    ds = sorted((float(d) for d in demand.values() if d), reverse=True)
    cap = ds[1] * 1.5 if len(ds) > 1 else (ds[0] if ds else 1.0)
    demand = {k: min(float(v), cap) if v else v for k, v in demand.items()}
    max_d = max([d for d in demand.values() if d] or [1.0])
    candidates = []
    for fam, sub, spec in all_styles(cfg):
        if sub in recent_subs or fam == blocked_fam:
            continue
        d = demand.get(sub)
        # demand relative to the strongest style; unknown demand -> modest neutral score
        d_score = (0.15 + 0.85 * math.sqrt(d / max_d)) if d else 0.35
        vs = views.get(sub, [])
        own = ((sum(vs) + chan_avg * 2) / (len(vs) + 2)) / chan_avg if chan_avg and vs else 1.0
        uses = sum(1 for h in recent if h.get("substyle") == sub)
        explore = 1.0 + explore_w / (1 + uses)
        last_idx = max((i for i, h in enumerate(recent) if h.get("substyle") == sub), default=None)
        age = (len(recent) - last_idx) if last_idx is not None else 99
        recency = min(1.0, 0.4 + 0.1 * age)
        boost = float(niche.get(sub, niche.get(fam, 1.0)) or 1.0)  # config styles.niche_boost
        ps = pcts.get(sub, [])
        if ps and chan_pct:  # Analytics report: styles people keep listening to get picked more often
            boost *= min(1.4, max(0.7, (sum(ps) / len(ps)) / chan_pct))
        weight = max(0.01, d_score * min(2.5, max(0.4, own)) * explore * recency * boost)
        candidates.append((weight, fam, sub, spec, {"demand": d, "own": round(own, 2), "uses": uses, "boost": boost}))
    if not candidates:  # everything blocked (tiny custom catalog) -> relax the rules
        candidates = [(1.0, f, s, sp, {}) for f, s, sp in all_styles(cfg)]
    total = sum(c[0] for c in candidates)
    r = rng.uniform(0, total)
    acc = 0.0
    pick = candidates[-1]
    for c in candidates:
        acc += c[0]
        if r <= acc:
            pick = c
            break
    weight, fam, sub, spec, why = pick
    top = sorted(candidates, key=lambda c: -c[0])[:5]
    if log:
        log.info("Style pick: %s / %s (weight %.2f of %.2f); top: %s", fam, sub, weight, total,
                 ", ".join(f"{c[2]} {c[0]:.2f}" for c in top))
    return {"family": fam, "substyle": sub, "spec": spec, "weight": round(weight, 3),
            "share": round(weight / total, 3) if total else 0, "why": why,
            "top": [(c[2], round(c[0], 3)) for c in top]}


def target_length(cfg: dict, spec: dict, rng: random.Random) -> int:
    lo_cfg, hi_cfg = int(cfg["music"]["min_seconds"]), int(cfg["music"]["max_seconds"])
    lo, hi = spec.get("length", (lo_cfg, hi_cfg))
    lo, hi = max(lo_cfg, int(lo)), min(hi_cfg, int(hi))
    if lo > hi:
        lo, hi = lo_cfg, hi_cfg
    return rng.randint(lo, hi)
