"""Search-friendly wording: title phrases, first hashtags, thumbnail headline, playlist and long-mix titles.

Titles follow  "<what people search> – <track name> | AI Music",  e.g.
    "Chillstep Music to Relax & Study – Moonlit Serenity | AI Music"
The phrase comes first because YouTube search and viewers read the beginning of the title.
"""
from __future__ import annotations

# substyle -> (title phrase, three hashtags shown above the title, thumbnail headline)
SUBSTYLE = {
    "synthwave":         ("Synthwave Music for Night Drive & Focus", ["#synthwave", "#nightdrive", "#retrowave"], "SYNTHWAVE • NIGHT DRIVE"),
    "deep house":        ("Deep House Music for Work & Focus", ["#deephouse", "#workmusic", "#focusmusic"], "DEEP HOUSE • FOCUS"),
    "drum and bass":     ("Liquid Drum and Bass for Focus & Coding", ["#liquiddnb", "#drumandbass", "#focusmusic"], "LIQUID DNB • FOCUS"),
    "downtempo":         ("Downtempo Chill Music to Relax & Work", ["#downtempo", "#chillmusic", "#workmusic"], "DOWNTEMPO • CHILL"),
    "melodic techno":    ("Melodic Techno for Deep Focus", ["#melodictechno", "#focusmusic", "#techno"], "MELODIC TECHNO"),
    "lofi hip hop":      ("Lofi Hip Hop Beats to Relax / Study", ["#lofi", "#lofihiphop", "#studymusic"], "LOFI • STUDY"),
    "jazzhop":           ("Jazzhop Beats to Relax & Study", ["#jazzhop", "#lofi", "#studymusic"], "JAZZHOP • STUDY"),
    "chillstep":         ("Chillstep Music to Relax & Study", ["#chillstep", "#chillmusic", "#studymusic"], "CHILLSTEP • RELAX"),
    "smooth jazz":       ("Smooth Jazz Music to Relax & Work", ["#smoothjazz", "#jazz", "#relaxingmusic"], "SMOOTH JAZZ • RELAX"),
    "solo piano":        ("Relaxing Piano Music for Sleep & Study", ["#pianomusic", "#relaxingmusic", "#studymusic"], "PIANO • SLEEP"),
    "string quartet":    ("Calm String Quartet for Study & Focus", ["#classicalmusic", "#stringquartet", "#studymusic"], "STRINGS • FOCUS"),
    "baroque":           ("Baroque Music for Studying & Concentration", ["#baroque", "#classicalmusic", "#studymusic"], "BAROQUE • STUDY"),
    "minimalist piano":  ("Calm Minimal Piano for Deep Focus", ["#pianomusic", "#focusmusic", "#calmmusic"], "PIANO • FOCUS"),
    "epic orchestral":   ("Epic Orchestral Music for Motivation", ["#epicmusic", "#orchestral", "#motivation"], "EPIC • MOTIVATION"),
    "fantasy adventure": ("Fantasy Adventure Music for Reading & Gaming", ["#fantasymusic", "#adventuremusic", "#readingmusic"], "FANTASY • READING"),
    "dark score":        ("Dark Cinematic Music for Focus & Writing", ["#darkmusic", "#cinematicmusic", "#writingmusic"], "DARK • CINEMATIC"),
    "emotional score":   ("Emotional Cinematic Piano to Relax", ["#emotionalmusic", "#cinematicmusic", "#piano"], "EMOTIONAL • PIANO"),
    "sci-fi score":      ("Sci-Fi Ambient Music for Focus", ["#scifi", "#ambientmusic", "#focusmusic"], "SCI-FI • FOCUS"),
    "ambient drone":     ("Ambient Music for Sleep & Meditation", ["#ambientmusic", "#sleepmusic", "#meditationmusic"], "AMBIENT • SLEEP"),
    "space ambient":     ("Space Ambient Music for Sleep & Deep Focus", ["#spaceambient", "#sleepmusic", "#ambientmusic"], "SPACE • SLEEP"),
    "nature ambient":    ("Relaxing Nature Ambient Music for Sleep", ["#relaxingmusic", "#sleepmusic", "#naturemusic"], "NATURE • SLEEP"),
    "acoustic guitar":   ("Relaxing Acoustic Guitar Music for Work", ["#acousticguitar", "#relaxingmusic", "#workmusic"], "ACOUSTIC • RELAX"),
    "celtic":            ("Relaxing Celtic Music for Reading & Calm", ["#celticmusic", "#relaxingmusic", "#readingmusic"], "CELTIC • RELAX"),
    "cafe jazz":         ("Coffee Shop Jazz Music to Relax", ["#cafejazz", "#coffeeshopmusic", "#jazz"], "CAFE JAZZ"),
}

# playlists: new search-friendly titles; the old titles are renamed automatically (same playlist, same link)
PLAYLIST = {
    "chill": ("Lofi & Chill Beats to Relax / Study 🎧", "AI Chill & Lofi Music",
              "☕ Lofi, jazzhop and chill beats to study, relax and unwind. New tracks every week."),
    "classical": ("Relaxing Piano & Classical Music for Study 🎹", "AI Classical Music",
                  "🎻 Calm piano, strings and classical-style pieces for reading, study and quiet evenings."),
    "electronic": ("Electronic Music for Focus & Night Drive 🌃", "AI Electronic Music",
                   "🎛️ Synthwave, deep house and downtempo for night drives, coding and deep work."),
    "cinematic": ("Epic Cinematic Music for Motivation & Focus 🎬", "AI Cinematic Music",
                  "🎬 Epic, emotional and dark film-score style music for inspiration and creative work."),
    "ambient": ("Ambient Music for Sleep & Deep Focus 🌌", "AI Ambient Music",
                "🌌 Ambient soundscapes for sleep, meditation and deep focus."),
    "acoustic": ("Coffee Shop Acoustic Music to Relax ☕", "AI Acoustic Music",
                 "🎸 Warm acoustic guitar, celtic and cafe jazz instrumentals for slow mornings."),
}
LONG_MIX_PLAYLIST = ("Long Mixes: 1-3 Hours of Relaxing Music ⏳",
                     "⏳ Hour-long and longer mixes of our tracks - for study sessions, work and sleep.")

# long mixes: what the mix is for, per family ("all" = mixed styles)
MIX_PHRASE = {
    "chill": "Lofi & Chill Beats to Relax / Study",
    "classical": "Relaxing Piano & Classical Music for Study and Sleep",
    "electronic": "Electronic Music for Focus & Night Drive",
    "cinematic": "Epic Cinematic Music for Focus & Motivation",
    "ambient": "Ambient Music for Sleep & Deep Focus",
    "acoustic": "Coffee Shop Acoustic Music to Relax & Work",
    "all": "Relaxing Instrumental Music for Study, Work & Sleep",
}
MIX_HASHTAGS = {
    "chill": ["#lofi", "#studymusic", "#chillmusic"], "classical": ["#pianomusic", "#studymusic", "#sleepmusic"],
    "electronic": ["#focusmusic", "#nightdrive", "#electronicmusic"], "cinematic": ["#epicmusic", "#focusmusic", "#cinematicmusic"],
    "ambient": ["#sleepmusic", "#ambientmusic", "#deepfocus"], "acoustic": ["#coffeeshopmusic", "#relaxingmusic", "#acoustic"],
    "all": ["#relaxingmusic", "#studymusic", "#sleepmusic"],
}


def phrase(substyle: str) -> str:
    return SUBSTYLE.get(str(substyle).lower(), (f"{str(substyle).title()} Music to Relax & Focus", [], ""))[0]


def first_hashtags(substyle: str) -> list[str]:
    return list(SUBSTYLE.get(str(substyle).lower(), ("", ["#relaxingmusic", "#instrumental", "#chillmusic"], ""))[1])


def headline(substyle: str) -> str:
    return SUBSTYLE.get(str(substyle).lower(), ("", [], str(substyle).upper()))[2] or str(substyle).upper()


# 2.7: the same phrase on every video of a style looked mass-produced -> rotate the "what for" part.
# Genre part stays (it is what people search), the use case changes: "Baroque Music for Reading & Calm".
USES = {
    "chill": ["to Relax / Study", "for Late Night Focus", "to Unwind After Work", "for Homework & Reading",
              "for a Slow Sunday", "to Chill & Focus"],
    "electronic": ["for Night Drive & Focus", "for Coding & Deep Work", "for Late Night Work", "to Stay in the Zone",
                   "for Gaming & Focus", "for a City Night Walk"],
    "classical": ["for Studying & Concentration", "for Reading & Calm", "for Deep Focus", "for a Quiet Evening",
                  "for Writing & Thinking", "to Slow Down"],
    "cinematic": ["for Focus & Writing", "for Motivation & Work", "for Reading Fantasy Books", "for Creative Work",
                  "for Studying & Inspiration", "for Tabletop Games"],
    "ambient": ["for Sleep & Meditation", "for Deep Focus", "for Stress Relief", "for Yoga & Breathing",
                "for Falling Asleep Fast", "for Calm Reading"],
    "acoustic": ["to Relax", "for a Slow Morning", "for Work & Coffee", "for Reading & Calm", "for a Sunny Afternoon",
                 "to Unwind"],
}


def genre_part(substyle: str) -> str:
    """'Baroque Music for Studying & Concentration' -> 'Baroque Music' (the searched genre words)."""
    import re
    p = phrase(substyle)
    return re.split(r" (?:for|to) ", p, maxsplit=1)[0]


def phrase_options(substyle: str, family: str) -> list[str]:
    base = phrase(substyle)
    g = genre_part(substyle)
    return list(dict.fromkeys([base] + [f"{g} {u}" for u in USES.get(family, [])]))


def pick_phrase(substyle: str, family: str, history: list, rng) -> str:
    """Rotate the title phrase: not one used by the last tracks of the same style (falls back to the classic one)."""
    opts = phrase_options(substyle, family)
    recent = [str(h.get("title") or "") for h in history[-30:] if h.get("substyle") == substyle]
    fresh = [o for o in opts if not any(r.startswith(o + " –") for r in recent)]
    return rng.choice(fresh or opts)


def title(substyle: str, name: str, suffix: str = " | AI Music", limit: int = 100, lead: str | None = None) -> str:
    """'<phrase> – <name> | AI Music'; shortened phrase when too long (YouTube max 100). lead = rotated phrase."""
    p = lead or phrase(substyle)
    t = f"{p} – {name}{suffix}"
    if len(t) > limit:  # drop the "& ..." part of the phrase first
        p = p.split(" & ")[0].split(" / ")[0]
        t = f"{p} – {name}{suffix}"
    return t[:limit]


# Shorts: big on-screen line for the first ~2.5 s (plain text - fonts have no emoji) and extra title angles
SHORT_HOOKS = {
    "chill": ["POV: late night study session", "Lofi for when you need to focus", "Put this on and relax"],
    "classical": ["Calm piano for your study time", "POV: quiet evening with a book", "Music to slow down your mind"],
    "electronic": ["POV: 2 AM night drive", "Music for deep work mode", "Coding at night hits different"],
    "cinematic": ["This feels like a movie scene", "Music for main character energy", "Epic focus music"],
    "ambient": ["Close your eyes for 40 seconds", "Music for falling asleep", "Breathe in. Breathe out."],
    "acoustic": ["POV: cozy coffee shop morning", "Coffee + this song = calm", "Rainy day cafe vibes"],
}
SHORT_ANGLES = {
    "chill": ["Late Night Lofi", "Lofi to Focus"], "classical": ["Calm Piano for Reading", "Music to Slow Down"],
    "electronic": ["Night Drive Music", "Deep Work Music"], "cinematic": ["Epic Focus Music", "Cinematic Motivation"],
    "ambient": ["Sleep Music", "Calm Breathing Music"], "acoustic": ["Coffee Shop Vibes", "Rainy Day Cafe Music"],
}

# 2.7.1: the 3 fixed hooks per family repeated on every Short -> bigger pools (family = fits every substyle of it,
# substyle = genre-specific). Max ~32 characters so the line fits the card. Chosen per track avoiding recent ones.
HOOK_POOL = {
    "chill": ["POV: late night study session", "Lofi for when you need to focus", "Put this on and relax",
              "Homework but make it cozy", "3 AM and still studying", "Your brain on calm mode",
              "Rain outside, beats inside", "Study break? Not yet.", "This beat fixes bad days"],
    "classical": ["Calm piano for your study time", "POV: quiet evening with a book", "Music to slow down your mind",
                  "Reading hits different with this", "Old library, new focus", "Strings for a calm mind",
                  "Candlelight and pages", "Let your thoughts settle", "Elegant focus music"],
    "electronic": ["POV: 2 AM night drive", "Music for deep work mode", "Coding at night hits different",
                   "City lights, no traffic", "Headphones on, world off", "Focus mode: activated",
                   "One more lap around the city", "Synths for late work", "Neon, rain, and this track"],
    "cinematic": ["This feels like a movie scene", "Music for main character energy", "Epic focus music",
                  "Write your next chapter", "The calm before the battle", "Feels like a trailer",
                  "Your quest starts now", "Score for your daydream", "Cinematic focus mode"],
    "ambient": ["Close your eyes for 40 seconds", "Music for falling asleep", "Breathe in. Breathe out.",
                "Let the noise fade away", "Sleep starts here", "Float for a moment",
                "Slow down. Just listen.", "Quiet mind, deep focus", "Unwind in 40 seconds"],
    "acoustic": ["Slow morning, warm sound", "Music for a sunny afternoon", "Put the kettle on",
                 "Simple, warm, calm", "Weekend morning vibes", "Strings and sunshine",
                 "Unplug for a minute", "Cozy corner music", "Breathe and enjoy the moment"],
}
HOOK_SUB = {
    "cafe jazz": ["POV: cozy coffee shop morning", "Coffee + this song = calm", "Rainy day cafe vibes",
                  "Table by the cafe window", "Espresso and a slow piano"],
    "smooth jazz": ["Smooth jazz for a slow evening", "Sax, lights, and city calm", "After-work jazz mood"],
    "jazzhop": ["Jazzy beats for focus", "Brushes, keys, and calm"],
    "lofi hip hop": ["Lofi for your study grind", "Beats to finish that essay"],
    "celtic": ["POV: walking the old hills", "Music from the misty highlands", "A tavern tale in 40 seconds"],
    "acoustic guitar": ["Just a guitar and the morning", "Fingerpicked calm"],
    "baroque": ["Bach-style focus mode", "Baroque for deep study"],
    "solo piano": ["Just piano. Just calm.", "Piano for a quiet night"],
    "sci-fi score": ["POV: drifting past a new planet", "Music for a long space voyage"],
    "space ambient": ["Floating between the stars", "Sleep in zero gravity"],
    "nature ambient": ["Forest sounds for your mind", "Like a walk in the woods"],
    "dark score": ["Something is watching", "Dark music for deep writing"],
    "drum and bass": ["Liquid DnB for flow state", "Rolling beats, clear mind"],
    "synthwave": ["Retro drive at midnight", "Back to the neon 80s"],
    "deep house": ["Deep house for deep work", "Sunset rooftop groove"],
}
ANGLE_POOL = {
    "chill": ["Late Night Lofi", "Lofi to Focus", "Chill Beats to Study", "Cozy Study Beats", "Lofi for Homework"],
    "classical": ["Calm Piano for Reading", "Music to Slow Down", "Classical Focus Music", "Quiet Study Music",
                  "Music for Reading"],
    "electronic": ["Night Drive Music", "Deep Work Music", "Coding Music", "Late Night Focus", "City Night Music"],
    "cinematic": ["Epic Focus Music", "Cinematic Motivation", "Music for Writing", "Fantasy Reading Music",
                  "Movie Score Vibes"],
    "ambient": ["Sleep Music", "Calm Breathing Music", "Meditation Music", "Deep Focus Ambient", "Relaxing Sleep Sounds"],
    "acoustic": ["Morning Acoustic Music", "Calm Work Music", "Slow Morning Music", "Relaxing Instrumental",
                 "Music for Reading"],
}
ANGLE_SUB = {
    "cafe jazz": ["Coffee Shop Vibes", "Rainy Day Cafe Music", "Cafe Jazz for Work"],
    "smooth jazz": ["Evening Jazz Lounge", "Smooth Jazz for Work"],
    "celtic": ["Celtic Fantasy Music", "Music from the Highlands"],
    "sci-fi score": ["Space Focus Music", "Sci-Fi Ambient"],
    "space ambient": ["Space Sleep Music", "Music from Orbit"],
    "fantasy adventure": ["Fantasy Reading Music", "Adventure Music"],
    "dark score": ["Dark Writing Music", "Mysterious Ambience"],
    "synthwave": ["Retro Night Drive", "80s Synth Vibes"],
}


def pick_shorts(substyle: str, family: str, history: list, rng, avoid: list | None = None) -> dict:
    """3 hooks + 2 title angles for this track's Shorts, not used by the last ~8 tracks (nor in `avoid`)."""
    recent = {str(x).lower() for h in history[-8:] for x in (h.get("hooks") or []) + (h.get("short_angles") or [])}
    recent |= {str(x).lower() for x in (avoid or [])}
    hooks = list(dict.fromkeys(HOOK_SUB.get(str(substyle).lower(), []) + HOOK_POOL.get(family, [])))
    fresh = [x for x in hooks if x.lower() not in recent] or hooks or ["Music to relax & focus"]
    sub_first = [x for x in fresh if x in HOOK_SUB.get(str(substyle).lower(), [])]
    rest = [x for x in fresh if x not in sub_first]
    rng.shuffle(sub_first)
    rng.shuffle(rest)
    chosen = (sub_first[:1] + rest + sub_first[1:])[:3]  # one genre-specific line (when there is one) + two others
    sub_a = [a for a in ANGLE_SUB.get(str(substyle).lower(), []) if a.lower() not in recent]
    fam_a = [a for a in ANGLE_POOL.get(family, []) if a.lower() not in recent] or ANGLE_POOL.get(family, [])
    rng.shuffle(sub_a)
    rng.shuffle(fam_a)
    angles = (sub_a[:1] + fam_a)[:2]  # one genre-specific angle (when there is one) + one from the family
    return {"hooks": chosen, "short_angles": angles}


def short_hook(family: str, idx: int = 0, brief: dict | None = None) -> str:
    hooks = (brief or {}).get("hooks") or SHORT_HOOKS.get(family) or ["Music to relax & focus"]
    return hooks[idx % len(hooks)]


def short_title(substyle: str, name: str, suffix: str = " | AI Music", idx: int = 0, family: str = "",
                angles: list | None = None) -> str:
    """Short #1: '<name> – <phrase>'; Shorts #2, #3: other search angles (2.7.1: rotated per track)."""
    p = phrase(substyle).split(" & ")[0].split(" / ")[0]
    angles = angles or SHORT_ANGLES.get(family) or []
    if idx > 0 and angles:
        p = angles[(idx - 1) % len(angles)]
    return f"{name} – {p}{suffix} #Shorts"[:100]
