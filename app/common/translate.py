"""Localized title + description (YouTube 'localizations'): viewers in Germany see a German title etc.
Translated by the local Ollama model, cached in <output>/translations.json. A failed language is skipped."""
from __future__ import annotations

import re

from . import llm
from .util import read_json, write_json

NAMES = {"de": "German", "es": "Spanish", "pt": "Portuguese (Brazil)", "fr": "French", "ja": "Japanese",
         "it": "Italian", "pl": "Polish", "ko": "Korean", "nl": "Dutch", "tr": "Turkish"}

PROMPT = """Translate these texts of a YouTube instrumental music video into {lang}.
Write like a native speaker who searches YouTube for this kind of music (natural search words, not literal).
Keep emojis. Do not add anything. Answer with JSON only:
{{"phrase": "...", "story": "...", "perfect_for": "..."}}

phrase: {phrase}
story: {story}
perfect_for: {perfect}"""


def localize(ctx, brief: dict, perfect_for: str) -> dict:
    """{lang: {"phrase", "story", "perfect_for"}} for channel.languages (cached per output folder)."""
    langs = [l for l in (ctx.cfg["channel"].get("languages") or []) if l in NAMES and l != "en"]
    if not langs:
        return {}
    cache_f = ctx.out_dir / "translations.json"
    cache = read_json(cache_f, {}) or {}
    todo = [l for l in langs if l not in cache]
    if todo and not llm.ensure_server(ctx.cfg, ctx.paths, ctx.log):
        ctx.note("Translations skipped - the language model is not running.")
        return {l: cache[l] for l in langs if l in cache}
    phrase = brief.get("seo_phrase") or brief["substyle"]
    for lang in todo:
        data = llm.ask_json(ctx.cfg, PROMPT.format(lang=NAMES[lang], phrase=phrase, story=brief["description_story"],
                                                   perfect=perfect_for), ["phrase", "story"], ctx.log, retries=2)
        if not data:
            ctx.log.warning("Translation %s failed - skipped", lang)
            continue
        clean = {k: re.sub(r"\s+", " ", str(data.get(k) or "")).strip()[:600] for k in ("phrase", "story", "perfect_for")}
        if clean["phrase"] and clean["story"]:
            cache[lang] = clean
            write_json(cache_f, cache)
    ctx.log.info("Localized title/description: %s", ", ".join(l for l in langs if l in cache) or "none")
    return {l: cache[l] for l in langs if l in cache}
