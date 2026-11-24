"""Long Mix: 1-3 hours from tracks already published on the channel (no new music is generated).

    run.py --long-mix [--family chill|classical|electronic|cinematic|ambient|acoustic|all] [--hours 2] [--dry-run]
    Panel: button "Long Mix"

- takes published tracks of one style family (history.json) whose audio is still on disk
  (output\\<date>\\track.wav, archive\\<date>\\track.wav, or the audio of video.mp4),
- prefers tracks that were not in an earlier mix (state/mixes.json),
- joins them (each track already has a gentle fade in/out and the same loudness), video with a slow zoom,
  chapters in the description (00:00 Track ...), big "2 HOURS" thumbnail,
- uploads to the next free day at the usual premiere time, adds it to the style playlist and to "Long Mixes".
"""
from __future__ import annotations

import json
import random
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import seo
from .common import media
from .common.util import Ctx, StepError, file_ok, load_history, read_json, write_json

FAMILIES = ["chill", "classical", "electronic", "cinematic", "ambient", "acoustic"]


def _audio_for(paths, h: dict) -> Path | None:
    for base in (paths.output, paths.archive):
        d = base / str(h.get("date"))
        for name in ("track.wav", "video.mp4"):
            if file_ok(d / name, 100_000):
                return d / name
    return None


def inventory(paths) -> dict:
    """{family: {"tracks": n, "minutes": m}} of published tracks with audio still on disk (+ "all")."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inv: dict = {f: {"tracks": 0, "minutes": 0.0} for f in FAMILIES + ["all"]}
    for h in load_history(paths):
        if not h.get("video_id") or (h.get("publish_at") or "") > now or _audio_for(paths, h) is None:
            continue
        mins = float(h.get("seconds") or 240) / 60
        for k in (h.get("style_family"), "all"):
            if k in inv:
                inv[k]["tracks"] += 1
                inv[k]["minutes"] = round(inv[k]["minutes"] + mins, 1)
    return inv


def fresh_count(paths) -> tuple[int, float]:
    """(tracks, minutes) published, with audio on disk and not used in any Long Mix yet."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    used = {v for m in (read_json(paths.state / "mixes.json", []) or []) for v in m.get("tracks", [])}
    n, mins = 0, 0.0
    for h in load_history(paths):
        if h.get("video_id") and h["video_id"] not in used and (h.get("publish_at") or "") <= now \
                and _audio_for(paths, h) is not None:
            n += 1
            mins += float(h.get("seconds") or 240) / 60
    return n, mins


def _fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def main(cfg: dict, paths, log, family: str = "all", hours: float = 2.0, dry_run: bool = False,
         fresh_only: bool = False) -> int:
    from .common.progress import Progress
    from .steps import upload, visuals

    family = family if family in FAMILIES + ["all"] else "all"
    hours = max(1.0, min(3.0, float(hours)))
    need = hours * 3600
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mixes = read_json(paths.state / "mixes.json", []) or []
    used_before = {v for m in mixes for v in m.get("tracks", [])}
    uses: dict[str, int] = {}
    for m in mixes:
        for v in m.get("tracks", []):
            uses[v] = uses.get(v, 0) + 1
    max_uses = int((cfg.get("longmix") or {}).get("max_uses", 3) or 0)  # 2.7: a track appears in at most N mixes
    pool = [h for h in load_history(paths) if h.get("video_id") and (h.get("publish_at") or "") <= now
            and (family == "all" or h.get("style_family") == family) and _audio_for(paths, h) is not None
            and (max_uses <= 0 or uses.get(h["video_id"], 0) < max_uses)]
    if fresh_only:
        pool = [h for h in pool if h["video_id"] not in used_before]
    total = sum(float(h.get("seconds") or 240) for h in pool)
    if total < need * 0.97:
        raise StepError(f"Not enough published tracks for a {hours:g} h '{family}' mix: {len(pool)} tracks, "
                        f"{total / 60:.0f} min (need {need / 60:.0f} min). Pick 'all' or fewer hours, or wait for more tracks.")
    rng = random.Random(int(time.time()))
    fresh = [h for h in pool if h["video_id"] not in used_before]
    old = [h for h in pool if h["video_id"] in used_before]
    rng.shuffle(fresh)
    rng.shuffle(old)
    chosen, acc = [], 0.0
    for h in fresh + old:  # tracks never mixed before come first
        if acc >= need:
            break
        chosen.append(h)
        acc += float(h.get("seconds") or 240)

    out = paths.output / f"mix-{datetime.now():%Y%m%d-%H%M}-{family}"
    out.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(date=date.today(), out_dir=out, cfg=cfg, paths=paths, log=log, dry_run=dry_run)
    est_render = acc * 0.25
    ctx.progress = Progress(paths, log, [("mix_audio", 60 + len(chosen) * 5), ("mix_video", est_render),
                                         ("mix_upload", 0 if dry_run else 600)], dry_run)
    log.info("Long Mix: %s, %d tracks, %s", family, len(chosen), _fmt(acc))

    # ---- audio: every track -> 48 kHz 24-bit wav, then one file; chapters from real durations
    ctx.progress.start("mix_audio")
    parts, chapters, t = [], [], 0.0
    for i, h in enumerate(chosen, 1):
        src = _audio_for(paths, h)
        wav = out / f"part{i:03d}.wav"
        media.ffmpeg(paths, ["-i", str(src), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(wav)], log, timeout=1800)
        dur = media.duration(paths, wav, log)
        name = h.get("base_title") or str(h.get("title", "Track")).split(" | ")[0]
        chapters.append((t, name))
        parts.append(wav)
        t += dur
        ctx.progress.update(i / len(chosen), f"utwór {i}/{len(chosen)}")
    lst = out / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    media.ffmpeg(paths, ["-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "pcm_s24le", str(out / "track.wav")], log, timeout=3600)
    for p in parts:
        p.unlink(missing_ok=True)
    ctx.progress.finish("mix_audio")

    # ---- pictures + video (same look as the tracks, big "2 HOURS" headline)
    ctx.progress.start("mix_video")
    first = read_json(paths.output / str(chosen[0]["date"]) / "brief.json", None) or \
        read_json(paths.archive / str(chosen[0]["date"]) / "brief.json", None) or {}
    phrase = seo.MIX_PHRASE[family]
    hours_txt = f"{hours:g} Hour" + ("s" if hours != 1 else "")
    mb = {"base_title": f"{hours_txt} of {phrase}", "title": f"{hours_txt} of {phrase}", "substyle": "long mix",
          "mood": f"{len(chosen)} tracks", "style_family": family if family != "all" else (first.get("style_family") or "chill"),
          "visual_theme": first.get("visual_theme") or {"palette": ["#1B0B3A", "#3D5AFE", "#00BFA5"], "motif": "calm"}}
    write_json(out / "brief.json", mb)
    rng2 = random.Random(len(chosen))
    art = None  # the generated picture of one of the mixed tracks (when there is one)
    for h in chosen:
        for base in (paths.output, paths.archive):
            f = base / str(h.get("date")) / "art_wide.png"
            if art is None and f.exists():
                from PIL import Image
                art = Image.open(f).convert("RGB")
    visuals.compose(int(cfg["video"]["width"]), int(cfg["video"]["height"]), mb, rng2, 0.8, art=art).save(out / "cover.png")
    visuals.compose(1280, 720, mb, random.Random(len(chosen)), 0.7, art=art,
                    headline=f"{hours_txt.upper()} • {phrase.split(' ')[0].upper()}").save(
        out / "thumb.jpg", quality=90)
    with ctx.progress.timed(0, 1, est_render, "render wideo"):
        visuals.render_video(ctx, t)
    (out / "track.wav").unlink(missing_ok=True)  # ~2 GB - the video keeps the audio
    ctx.progress.finish("mix_video")

    # ---- description with chapters
    tags3 = seo.MIX_HASHTAGS[family]
    lines = [f"⏳ {hours_txt} of {phrase} – {len(chosen)} calm instrumental tracks in one long session.", "",
             "✨ Perfect for: 📚 Study • 💻 Work • 😴 Sleep • 🧘 Relax", "", "🎶 Tracklist:"]
    lines += [f"{_fmt(s)} {n}" for s, n in chapters]
    # 2.7: every mix of the same kind got the identical title -> number them (Vol. 2, Vol. 3 ...)
    vol = 1 + sum(1 for m in mixes if m.get("family") == family and float(m.get("hours") or 0) == hours)
    title = f"{hours_txt} of {phrase}{f' Vol. {vol}' if vol > 1 else ''} 🎧{cfg['channel'].get('title_suffix', '')}"[:100]
    summary = {"family": family, "hours": hours, "tracks": [h["video_id"] for h in chosen], "seconds": round(t),
               "title": title, "folder": str(out)}
    if dry_run:
        write_json(out / "mix.json", {**summary, "description_lines": lines})
        ctx.progress.end(True, f"Long Mix gotowy (bez uploadu): {out}")
        log.info("Dry run - Long Mix ready in %s", out)
        return 0

    from .common.yt import YouTube
    from .common import channel as ch
    yt = YouTube(paths.secrets, log)
    ctx.yt = yt
    ctx.progress.start("mix_upload")
    ch.verify(yt, cfg, paths, log)
    pid, ptitle = upload.family_playlist(ctx, family) if family != "all" else (None, None)
    long_pid = yt.ensure_playlist(seo.LONG_MIX_PLAYLIST[0], seo.LONG_MIX_PLAYLIST[1], paths.state / "playlists.json")
    links = {"channel": upload.channel_url(cfg, paths), "playlist": f"https://www.youtube.com/playlist?list={long_pid}",
             "playlist_title": seo.LONG_MIX_PLAYLIST[0], "previous": []}
    days = upload.subscribe_when(cfg)
    lines += ["", f"🔔 Subscribe for new music {days}: {links['channel']}?sub_confirmation=1",
              f"🎧 Playlist – {links['playlist_title']}: {links['playlist']}", ""] + ([cfg["channel"]["disclosure_line"], ""] if cfg["channel"].get("disclosure_line") else []) + [
              " ".join(tags3 + ["#longmix"])]
    desc = "\n".join(lines)[:4900]
    taken = {h.get("publish_at") for h in load_history(paths) if h.get("publish_at")} | {m.get("publish_at") for m in mixes}
    cfg_any_day = json.loads(json.dumps(cfg))
    cfg_any_day["channel"]["premieres"] = [d.title() for d in upload.DAYS]
    slot = upload.next_slot(cfg_any_day, taken)
    tags = seo.MIX_HASHTAGS[family] + [phrase, "long mix", f"{hours:g} hour music", "study music", "relaxing music"]
    b = upload.body(cfg, title, desc, [x.lstrip("#") for x in tags], slot)
    res = yt.upload_video(out / "video.mp4", b)
    vid = res["id"]
    rec = {**summary, "video_id": vid, "url": f"https://youtu.be/{vid}", "publish_at": b["status"].get("publishAt"),
           "created": datetime.now().isoformat(timespec="seconds")}
    mixes.append(rec)
    write_json(paths.state / "mixes.json", mixes)
    for step_name, fn in (("thumbnail", lambda: yt.set_thumbnail(vid, out / "thumb.jpg")),
                          ("long mixes playlist", lambda: yt.add_to_playlist(long_pid, vid)),
                          ("style playlist", lambda: pid and yt.add_to_playlist(pid, vid))):
        try:
            fn()
        except StepError as e:
            log.warning("Long Mix %s: %s", step_name, e)
    write_json(out / "mix.json", rec)
    ctx.progress.finish("mix_upload")
    ctx.progress.end(True, f"Long Mix: {rec['url']}")
    log.info("Long Mix uploaded: %s (publish %s)", rec["url"], rec["publish_at"])
    return 0
