"""Second good variant of a run -> its own video + Shorts, published the next day.

The quality step keeps the runner-up when it is good enough (config `bonus`) as <date>\\bonus\\track.wav.
Here it gets its own name, story and picture (local language model + picture generator), a video and
its own Shorts, all scheduled for the next day at the usual publish time. Files: <date>\\bonus\\  (brief.json, cover.png, thumb.jpg, video.mp4, upload.json).
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone

from . import seo
from .common import llm
from .common.util import Ctx, StepError, file_ok, load_history, read_json, save_history, write_json

PROMPT = """You name instrumental music tracks for a YouTube channel. Respond with a single JSON object only.
A second, different track was made in the same style as "{title}": {substyle}, mood: {mood}, {bpm} BPM, {key},
instruments: {instruments}. Give it its OWN identity, different from the first one.
Words that are BANNED in the title (overused on this channel): {banned}
Rules: English only. No real artists, songs, films, games, brands or places. Do not add "AI".
- "titles": a list of 5 different title ideas, each max 40 characters, 2-4 words, concrete and original.
- "description_story": 2 vivid sentences for listeners (scene + when to play it), max 1 emoji.
- "image_prompt": max 250 characters, ONE cinematic scene for the cover picture built on this direction: {scene}.
  No text, no logos, no real people.
JSON keys: titles, description_story, image_prompt"""


def make_brief(ctx: Ctx, br: dict, rng: random.Random, variant_name: str | None = None) -> dict:
    from . import variety
    from .steps.brief import _clean_text, variant_spec
    hist = load_history(ctx.paths)
    b = dict(br)
    b.pop("chosen_variant", None)
    # 2.7: the bonus is the OTHER variant -> its own key / tempo / mood / instruments (when the brief has them)
    spec = variant_spec(br, variant_name)
    if spec:
        b.update(spec)
    b["scene"] = variety.pick_scene(br["style_family"], hist + [{"scene": br.get("scene")}], rng, b["substyle"])
    # 2.7.1: own Shorts hooks/angles (not the main track's) and own hashtags (the main one's were about its scene)
    from .steps.brief import seo_shorts
    b.update(seo_shorts(b, hist, rng, avoid=(br.get("hooks") or []) + (br.get("short_angles") or [])))
    b.pop("title_candidates", None)
    tags: list[str] = []
    for t in seo.first_hashtags(b["substyle"]) + ["#" + re.sub(r"[^A-Za-z0-9]", "", b["substyle"].title()),
                                                  "#InstrumentalMusic", "#RelaxingMusic"]:
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    b["hashtags"] = tags[:5]
    raw = None
    try:
        if llm.ensure_server(ctx.cfg, ctx.paths, ctx.log):
            raw = llm.ask_json(ctx.cfg, PROMPT.format(
                title=br["base_title"], substyle=b["substyle"], mood=b["mood"], bpm=b["bpm"], key=b.get("key", ""),
                instruments=", ".join(b.get("instruments") or []), scene=b["scene"],
                banned=", ".join(sorted(variety.banned_title_words(hist) | variety.words(br["base_title"])))),
                ["description_story"], ctx.log)
    except Exception as e:  # noqa: BLE001 - fallback below
        ctx.log.warning("Bonus track: language model not available (%s) - simple name", e)
    raw = raw or {}
    cands = [*(raw.get("titles") if isinstance(raw.get("titles"), list) else []), raw.get("title")]
    name = variety.choose_title([c for c in cands if c], hist, rng, extra_used={br["base_title"]})
    b["base_title"] = name
    used_phrase = br.get("seo_phrase")
    opts = [p for p in seo.phrase_options(b["substyle"], b["style_family"]) if p != used_phrase]
    b["seo_phrase"] = rng.choice(opts) if opts else seo.phrase(b["substyle"])
    b["title"] = seo.title(b["substyle"], name, ctx.cfg["channel"]["title_suffix"], lead=b["seo_phrase"])
    b["description_story"] = _clean_text(raw.get("description_story"), 500) or br["description_story"]
    ip = _clean_text(raw.get("image_prompt"), 300)
    b["image_prompt"] = ip if len(ip) > 25 else f"{b['scene']}, {b['mood']} atmosphere"
    b["is_bonus_of"] = br["base_title"]
    return b


def slot(cfg: dict, main_publish: str | None, rng: random.Random) -> datetime:
    """Next day after the main video, at the usual publish time (config bonus.publish = next_day),
    or delay_hours later (bonus.publish = hours)."""
    bc = cfg.get("bonus", {})
    base = (datetime.strptime(main_publish, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if main_publish else datetime.now(timezone.utc))
    if str(bc.get("publish", "next_day")) == "hours":
        lo, hi = (bc.get("delay_hours") or [8, 16])[:2]
        when = base + timedelta(hours=rng.uniform(float(lo), float(hi)))
    else:
        from .steps.upload import tz
        zone = tz(cfg)
        hh, mm = (int(x) for x in str(cfg["channel"]["publish_time_local"]).split(":"))
        when = (base.astimezone(zone) + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(when, datetime.now(timezone.utc) + timedelta(hours=1))


def run(ctx: Ctx, br: dict, q: dict, up_main: dict) -> dict:
    from .common import media
    from .steps import upload, visuals
    bq = q.get("bonus") or {}
    bdir = ctx.out_dir / "bonus"
    if not bq or not file_ok(bdir / "track.wav", 100_000):
        return {}
    rec = read_json(bdir / "upload.json", {}) or {}
    rng = random.Random(ctx.date.toordinal() * 13 + 5)
    c2 = Ctx(date=ctx.date, out_dir=bdir, cfg=ctx.cfg, paths=ctx.paths, log=ctx.log, yt=ctx.yt,
             dry_run=ctx.dry_run, timings=ctx.timings, notes=ctx.notes, progress=None)
    if rec.get("video_id"):  # resumed run: the video is up - finish missing Shorts only
        if ctx.cfg["shorts"]["enabled"] and ctx.yt is not None and not ctx.dry_run:
            b0 = read_json(bdir / "brief.json", None)
            if b0:
                upload.upload_shorts(c2, b0, rec, bdir / "upload.json")
        return rec
    b = read_json(bdir / "brief.json", None) or make_brief(c2, br, rng, bq.get("name"))
    write_json(bdir / "brief.json", b)
    ctx.log.info("Bonus track: %s", b["title"])
    if not file_ok(bdir / "video.mp4", 100_000):
        if not (file_ok(bdir / "cover.png") and file_ok(bdir / "thumb.jpg")):
            write_json(bdir / "visuals.json", visuals.make_images(c2, b))
        dur = media.duration(ctx.paths, bdir / "track.wav", ctx.log)
        visuals.render_video(c2, dur)
    if ctx.cfg["shorts"]["enabled"]:  # its own Shorts (other fragments, same look as the main track's)
        n = visuals.short_count(ctx.cfg)
        bqual = {"energy_per_sec": bq.get("energy_per_sec", [])}
        starts = media.windows(bqual["energy_per_sec"], int(ctx.cfg["shorts"]["seconds"]), n)
        for i, st in enumerate(starts[:n]):
            if not file_ok(bdir / visuals.short_name(i), 50_000):
                ctx.log.info("Bonus Short %d/%d (from %d s)...", i + 1, len(starts[:n]), st)
                visuals.render_short(c2, bqual, i, st)
    if ctx.dry_run:
        return {"skipped": True, "reason": "dry-run", "title": b["title"]}
    if ctx.yt is None:
        raise StepError("No YouTube access for the bonus video")
    when = slot(ctx.cfg, up_main.get("publish_at"), rng)
    links = upload.gather_links(c2, b)
    desc = upload.description(ctx.cfg, b, links=links)
    body = upload.body(ctx.cfg, b["title"], desc, upload.tags(b, ctx.cfg), when,
                       upload.localizations(c2, b, desc, links))
    if body["status"].get("privacyStatus") == "public":  # public mode: still wait until tomorrow
        body["status"].update(privacyStatus="private", publishAt=upload.utc_iso(when))
    res = ctx.yt.upload_video(bdir / "video.mp4", body)
    rec = {"video_id": res["id"], "url": f"https://youtu.be/{res['id']}", "publish_at": body["status"].get("publishAt"),
           "title": b["title"]}
    rec["channel_url"] = links.get("channel")
    write_json(bdir / "upload.json", rec)
    ctx.log.info("Bonus video uploaded: %s (publish %s)", rec["url"], rec["publish_at"] or "-")
    for what, fn in (("thumbnail", lambda: ctx.yt.set_thumbnail(res["id"], bdir / "thumb.jpg")),
                     ("playlist", lambda: links.get("playlist_id") and ctx.yt.add_to_playlist(links["playlist_id"], res["id"]))):
        try:
            fn()
        except StepError as e:
            ctx.note(f"Bonus video {what}: {e}")
    if ctx.cfg["shorts"]["enabled"]:
        upload.upload_shorts(c2, b, rec, bdir / "upload.json")  # scheduled together with the bonus video
    hist = load_history(ctx.paths)
    if not any(h.get("video_id") == res["id"] for h in hist):
        hist.append({"date": ctx.date.isoformat(), "video_id": res["id"], "url": rec["url"], "title": b["title"],
                     "base_title": b["base_title"], "style_family": b["style_family"], "substyle": b["substyle"],
                     "key": b.get("key"), "seconds": round(bq.get("duration") or 0), "mood": b["mood"], "bpm": b["bpm"],
                     "music_prompt": b.get("music_prompt"), "publish_at": rec["publish_at"], "bonus": True,
                     "scene": b.get("scene"), "texture": b.get("texture"),
                     "hooks": b.get("hooks"), "short_angles": b.get("short_angles"),
                     "fingerprint": bq.get("fingerprint"), "views": None,
                     "short_ids": [x["id"] for x in rec.get("shorts", []) if x.get("id")]})
        save_history(ctx.paths, hist)
    return rec
