"""Configuration and paths.

All paths come from environment variables set by setup.ps1. When a variable is
missing we fall back to folders inside the workspace, so the agent also works
when started before the user logged out and back in.
"""
from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_HOME = APP_DIR.parent

DEFAULTS: dict = {
    "channel": {
        # target channel, e.g. "@YourChannel" (or a channel ID UC...). Empty = whatever channel the login has.
        # Uploads to any other channel are refused.
        "expected_handle": "",
        "language": "en",
        "category_id": "10",
        # YouTube rule: AI-generated music that is the main focus of a video must be disclosed with this flag
        # (shown as a small "AI" note under the title, like on other AI music channels). Keep it True.
        "contains_synthetic_media": True,
        "title_suffix": "",            # e.g. " | AI Music" - optional, YouTube does not require it
        "disclosure_line": "",         # optional extra line in the description
        "max_hashtags": 5,
        # title + description translated by the local model (YouTube shows them to viewers in these languages)
        "languages": ["de", "es", "pt", "fr", "ja"],
        # scheduled = private until publishAt (premiere slot); private = stays private; public = public at once
        "publish_mode": "scheduled",
        "premieres": ["Wednesday", "Monday"],
        "publish_time_local": "18:00",
        "timezone": "Europe/Warsaw",
        "publish_delay_hours": 6,
        "playlists": True,
    },
    # automatic 1-hour mix after every N new tracks (0 = off; button "Long Mix" works always). 2.7: 15 -> 8;
    # the mix takes the new tracks first and fills the hour with older ones (each track in at most max_uses mixes).
    "longmix": {"auto_every": 8, "max_uses": 3},
    # 2.7 variety rules (app/variety.py): key / tempo / mood / scene are chosen in code, avoiding recent tracks;
    # distinct_variants: the 2nd variant is a different piece (own key, tempo, mood, instruments) -> real bonus track
    "variety": {"distinct_variants": True, "title_candidates": 5},
    "shorts": {"enabled": True, "seconds": 40, "offset_days": 0, "count": 3, "offsets_days": [0, 0, 0]},  # all Shorts go live together with the video
    # Style choice is research-driven (app/styles.py). Built-in catalog: 6 families, 24 substyles.
    "styles": {
        "no_repeat_substyle": 6,     # a substyle is not repeated within the last N premieres
        "max_family_streak": 1,      # never the same family twice in a row
        "exploration_weight": 0.35,  # bonus for styles rarely tried yet
        "exclude": [],               # e.g. ["deep house", "ambient"] - substyles or whole families to skip
        "catalog": {},               # add/override substyles: {family: {name: {bpm: [a,b], length: [s1,s2], query: "..."}}}
        # niche_boost: multiplier per substyle or family. >1 = more often. Niches with demand but fewer giant
        # channels get more, crowded lofi/chillstep less (a new channel rarely gets shown there).
        "niche_boost": {"dark score": 1.8, "cafe jazz": 1.8, "ambient drone": 1.6, "nature ambient": 1.4,
                        "space ambient": 1.3, "emotional score": 1.2, "celtic": 1.2,
                        "lofi hip hop": 0.6, "jazzhop": 0.7, "chillstep": 0.8, "deep house": 0.7},
    },
    "music": {
        # engine: acestep (ACE-Step 1.5 via acestep.cpp - default since 2.0) or sa3 (Stable Audio 3 via sa3.cpp)
        "engine": "acestep",
        # acestep: auto | turbo (2B, fast) | xl-turbo (4B, better, ~2x slower)
        # sa3:     auto | medium | small-music   (auto = medium when installed)
        "model": "auto",
        "encoding": "f32",           # must match the downloaded model files (setup downloads F32 - safer on CPUs without AVX2)
        "min_seconds": 170,          # 2:50
        "max_seconds": 320,          # 5:20
        # auto: one generation when the model can make the whole length, otherwise an arrangement of sections
        "mode": "auto",
        "native_max_seconds": {"small-music": 120, "medium": 380, "turbo": 600, "xl-turbo": 600},
        "section_max_seconds": 115,  # longest single generation in sections mode
        "crossfade_seconds": 6,
        "variants": 2,
        "timeout_minutes": 120,      # per single generation
        "target_lufs": -14,
        "steps": 0,                  # diffusion steps (0 = engine default); used only if sa3-generate has --steps
        "extra_args": [],            # extra sa3-generate arguments, e.g. ["--cfg-scale", "7"]
        # {exe} {model} {prompt} {duration} {out} {seed} are replaced; --seed/--encoding are added if sa3 supports them
        "command": ["{exe}", "--model", "{model}", "--prompt", "{prompt}", "--duration", "{duration}", "--out", "{out}"],
    },
    # ACE-Step 1.5 (acestep.cpp, MIT): ace-lm plans the song (5 Hz audio codes), ace-synth renders it (DiT + VAE), 48 kHz.
    "acestep": {
        "lm_model": "acestep-5Hz-lm-1.7B-Q8_0",            # 0.6B = faster, 4B = better/slower
        "synth_models": {"turbo": "acestep-v15-turbo-Q8_0", "xl-turbo": "acestep-v15-xl-turbo-Q8_0"},
        "use_lm": True,              # False = DiT only (faster, less song structure)
        "use_cot_caption": True,     # the LM may enrich the style caption
        "steps": 0,                  # 0 = model default (8 for turbo)
        "shift": 0,                  # 0 = model default
        "output_format": "wav32",   # 32-bit float from the engine - no extra quantisation step
        "vae": "scragvae-BF16",      # decoder: scragvae-BF16 (clearer highs, more dynamics) | "" = stock vae-BF16
        "peak_clip": 0,              # 0 = no hard clipping in the engine (engine default 10 clips peaks)
        "threads": 0,                # CPU threads for the engine; 0 = all physical cores (needs the setup-patched build)
        "priority": "above_normal",  # engine process priority: normal | above_normal | high
        "extra_request": {},         # any other AceRequest field, e.g. {"solver": "dpm3m"}
        "lm_cmd": ["{lm}", "--models", "{models}", "--request", "{request}"],
        "synth_cmd": ["{synth}", "--models", "{models}", "--request", "{request}"],
    },
    # mastering: two-pass loudness (constant gain + true-peak limiter, no "auto volume" pumping),
    # denoise off|light|strong (removes hiss / dictaphone-like background), AAC bitrate as YouTube recommends
    # stereo_fix auto: lowers an unnaturally wide/uncorrelated side signal (ACE-Step decoder) to side_target_db
    "audio": {"stereo_fix": "auto", "side_target_db": -6, "denoise": "off", "highpass_hz": 25, "true_peak_db": -1.5, "max_gain_db": 15,
              "aac_bitrate": "384k", "shorts_aac_bitrate": "256k"},
    "quality": {
        "max_silence_ratio": 0.15,
        "max_clipping_ratio": 0.002,
        "min_duration_ratio": 0.9,   # of the target length
        "min_distance_to_history": 0.01,
        "history_window": 30,
        "thumbnail_min_hash_distance": 8,
    },
    # force_cpu: old GPUs (GTX 750) crash Ollama's CUDA backend - run the language model on the CPU
    "llm": {"model": "qwen2.5:3b", "host": "http://127.0.0.1:11434", "timeout_seconds": 900, "temperature": 0.8,
            "force_cpu": True},
    # search_per_run: how many substyles are measured on YouTube per run (search.list = 100 quota units each)
    "trends": {"regions": ["US", "GB", "DE", "JP"], "max_results": 50, "search_per_run": 10,
               "search_days": 30, "cache_days": 14},
    # endscreen_seconds: last N s get an end card (room for YouTube end screen elements); 0 = off
    # Cover pictures: FLUX.1-schnell via stable-diffusion.cpp (local CPU, ~3-10 min per picture).
    # engine: flux | procedural (abstract backgrounds, no download). thumb_text: headline | none. badge: "AI MUSIC" badge.
    # model: auto (speed-test winner) | klein (FLUX.2 klein 4B, faster) | schnell (FLUX.1, slower). threads: 0 = from test.
    # tall_mode: derive = Shorts picture made from the cover (1 generation) | generate = own 9:16 picture (2x time)
    "images": {"engine": "flux", "model": "auto", "threads": 0, "steps": 4, "wide": [1024, 576], "tall": [576, 1024],
               "tall_mode": "derive", "flash_attention": True, "timeout_min": 40, "vae_tiling": False, "style": "",
               "thumb_text": "headline", "badge": False},
    # Second variant of the run: its quality is checked like the first one; when it is good it becomes its own
    # video + Shorts, published the NEXT DAY at publish_time_local (publish: next_day | hours -> delay_hours later).
    "bonus": {"enabled": True, "min_score": 0.7, "max_score_gap": 0.15, "publish": "next_day", "delay_hours": [8, 16]},
    "video": {"width": 1920, "height": 1080, "fps": 30, "zoom": True, "crf": 23, "preset": "veryfast",
              "endscreen_seconds": 20},
    "housekeeping": {"keep_output_days": 60, "keep_log_days": 30, "min_free_gb": 10},
    # Self-repair after a failed run (app/repair.py). model = local Ollama coding model for code patches.
    "repair": {"auto": True, "llm_patches": True, "model": "qwen2.5-coder:7b", "max_attempts": 2, "timeout_seconds": 1800},
}



def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


DEPRECATED = {("music", "duration_seconds"), ("styles", "rotation"), ("styles", "trend_pick_every"),
              ("styles", "electronic"), ("styles", "classical"), ("styles", "cinematic")}


def _drop_deprecated(data: dict) -> dict:
    for sec, key in DEPRECATED:
        if isinstance(data.get(sec), dict):
            data[sec].pop(key, None)
    return data


def load(path: str | os.PathLike | None = None) -> dict:
    p = Path(path or os.environ.get("AIMUSIC_CONFIG") or (home() / "config" / "config.yaml"))
    merged = copy.deepcopy(DEFAULTS)
    # config.yaml = your settings; config.auto.yaml = corrections made by the repair tool (delete to undo)
    for f in (p, p.with_name("config.auto.yaml")):
        if f.exists():
            with open(f, "r", encoding="utf-8-sig") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{f}: must be a YAML mapping")
            if f == p:
                data = _migrate(data)
            merged = deep_merge(merged, _drop_deprecated(data))
    return merged


def _migrate(data: dict) -> dict:
    """Older config.yaml files: values that turned out to hurt quality are replaced in memory (the file is not touched).
    config_version 4 (2.1): music denoise 'light' smeared reverb/pads -> off by default.
    config_version 5 (2.2): Shorts published with the full track (offset_days 2 -> 0).
    config_version 6 (2.6): " | AI Music" title suffix and the AI description line off (the API flag stays on).
    config_version 7 (2.6.1): Shorts published together with their video (offsets_days [0, 2, 4] -> [0, 0, 0])."""
    if int(data.get("config_version") or 0) < 4:
        au = data.get("audio")
        if isinstance(au, dict) and str(au.get("denoise", "")).lower() == "light":
            au["denoise"] = "off"
    if int(data.get("config_version") or 0) < 5:  # 2.2: the Short goes live together with the full track
        sh = data.get("shorts")
        if isinstance(sh, dict) and int(sh.get("offset_days", 0) or 0) == 2:
            sh["offset_days"] = 0
    if int(data.get("config_version") or 0) < 6:  # 2.6: no "AI" in title/description by default (API flag stays)
        ch = data.get("channel")
        if isinstance(ch, dict):
            if str(ch.get("title_suffix", "")).strip() == "| AI Music":
                ch["title_suffix"] = ""
            if str(ch.get("disclosure_line", "")).startswith("🤖 Music & visuals created with AI"):
                ch["disclosure_line"] = ""
    if int(data.get("config_version") or 0) < 7:  # 2.6.1: Shorts together with their video
        sh = data.get("shorts")
        if isinstance(sh, dict) and sh.get("offsets_days") == [0, 2, 4]:
            sh["offsets_days"] = [0, 0, 0]
    return data


def home() -> Path:
    return Path(os.environ.get("AIMUSIC_HOME") or DEFAULT_HOME)


def _env_path(name: str, fallback: Path) -> Path:
    v = os.environ.get(name)
    return Path(v) if v else fallback


@dataclass
class Paths:
    home: Path
    output: Path
    archive: Path
    state: Path
    logs: Path
    secrets: Path
    temp: Path
    ffmpeg: str
    sa3_exe: Path | None
    sa3_dir: Path | None
    ace_bin: Path | None
    ace_models: Path | None
    ollama_exe: str | None

    def ensure(self) -> None:
        for d in (self.output, self.archive, self.state, self.logs, self.secrets, self.temp):
            d.mkdir(parents=True, exist_ok=True)


def _model_files(p: "Paths", key: str, encoding: str) -> bool:
    """True when a DiT .gguf of that model exists. The name may be in the file name or in a sub-folder
    (e.g. models/medium/dit-F32.gguf or models/sa3-medium-dit-F32.gguf)."""
    models = (p.sa3_dir / "models") if p.sa3_dir else None
    if not models or not models.exists():
        return False
    enc = encoding.upper()
    for f in models.rglob("*.gguf"):
        rel = str(f.relative_to(models)).lower().replace("\\", "/")
        if key in rel and "dit" in rel and f.name.upper().endswith(f"{enc}.GGUF"):
            return True
    return False


def engine(cfg: dict) -> str:
    return str(cfg["music"].get("engine") or "acestep").lower()


ACE_MODELS = ("turbo", "xl-turbo")
SA3_MODELS = ("medium", "small-music")


def model_choices(cfg: dict) -> tuple[str, ...]:
    return ("auto",) + (ACE_MODELS if engine(cfg) == "acestep" else SA3_MODELS)


def ace_file(cfg: dict, model: str) -> str:
    return str((cfg.get("acestep", {}).get("synth_models") or {}).get(model, model))


def resolve_model(cfg: dict, p: "Paths") -> str:
    """'auto' -> best installed model of the selected engine."""
    m = str(cfg["music"].get("model") or "auto")
    if engine(cfg) == "acestep":
        if m in ACE_MODELS:
            return m
        return "turbo" if has_model_files(p, "turbo", "", cfg) or not has_model_files(p, "xl-turbo", "", cfg) else "xl-turbo"
    if m != "auto":
        return m
    enc = str(cfg["music"].get("encoding") or "f32")
    return "medium" if _model_files(p, "medium", enc) else "small-music"


def has_model_files(p: "Paths", model: str, encoding: str = "f32", cfg: dict | None = None) -> bool:
    if model in ACE_MODELS:
        cfg = cfg or DEFAULTS
        return bool(p.ace_models and (p.ace_models / f"{ace_file(cfg, model)}.gguf").exists())
    key = "medium" if model == "medium" else "small"
    return _model_files(p, key, encoding)


def paths() -> Paths:
    h = home()
    ff = os.environ.get("AIMUSIC_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
    sa3 = os.environ.get("AIMUSIC_SA3_EXE")
    sa3_path = Path(sa3) if sa3 else None
    if sa3_path is None:
        cands = list((h / "engines" / "sa3.cpp").glob("**/sa3-generate.exe")) if (h / "engines").exists() else []
        sa3_path = cands[0] if cands else None
    sa3_dir = None
    if sa3_path is not None:
        repo = Path(os.environ.get("SA3_REPO_DIR") or (h / "engines" / "sa3.cpp"))
        sa3_dir = repo if repo.is_dir() else sa3_path.parent
    ace_bin = Path(os.environ["AIMUSIC_ACE_BIN"]) if os.environ.get("AIMUSIC_ACE_BIN") else None
    if ace_bin is None and (h / "engines" / "acestep.cpp").exists():
        cands = sorted((h / "engines" / "acestep.cpp").glob("**/ace-synth*.exe"))
        ace_bin = cands[0].parent if cands else None
    ace_models = Path(os.environ.get("AIMUSIC_ACE_MODELS") or (h / "engines" / "acestep.cpp" / "models"))
    return Paths(
        home=h,
        output=_env_path("AIMUSIC_OUTPUT", h / "output"),
        archive=_env_path("AIMUSIC_ARCHIVE", h / "archive"),
        state=_env_path("AIMUSIC_STATE", h / "state"),
        logs=_env_path("AIMUSIC_LOGS", h / "logs"),
        secrets=_env_path("AIMUSIC_SECRETS", h / "secrets"),
        temp=_env_path("AIMUSIC_TEMP", h / "cache" / "temp"),
        ffmpeg=ff,
        sa3_exe=sa3_path,
        sa3_dir=sa3_dir,
        ace_bin=ace_bin,
        ace_models=ace_models,
        ollama_exe=os.environ.get("AIMUSIC_OLLAMA") or shutil.which("ollama"),
    )
