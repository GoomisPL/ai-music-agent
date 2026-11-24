"""Step 6: upload to YouTube with full AI disclosure, schedule the premiere, thumbnail, playlist, Short."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..common.util import Ctx, StepError, file_ok, load_history, read_json, write_json

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FAMILY_EMOJI = {"electronic": "🎛️", "classical": "🎻", "cinematic": "🎬", "chill": "☕", "ambient": "🌌", "acoustic": "🎸"}
USE_CASES = {
    "electronic": "🚗 Night drive • 💻 Focus • 🎮 Gaming",
    "classical": "📖 Reading • 🎓 Study • 🕯️ Calm evening",
    "cinematic": "🎬 Epic moments • 💪 Motivation • ✍️ Creative work",
    "chill": "📚 Study • ☕ Chill • 🌙 Late night",
    "ambient": "😴 Sleep • 🧘 Meditation • 🌌 Deep focus",
    "acoustic": "☕ Coffee break • 🌅 Slow morning • 🏡 Cozy evening",
}
DAY_NAMES = {"monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday", "thursday": "Thursday",
             "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday"}


def tz(cfg: dict):
    try:
        return ZoneInfo(cfg["channel"]["timezone"]) if ZoneInfo else timezone.utc
    except Exception:
        return timezone(timedelta(hours=1))


def next_slot(cfg: dict, taken: set[str], now: datetime | None = None) -> datetime:
    """Next premiere day/time at least publish_delay_hours ahead and not already used."""
    zone = tz(cfg)
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    hh, mm = (int(x) for x in str(cfg["channel"]["publish_time_local"]).split(":"))
    days = {DAYS.index(d.lower()) for d in cfg["channel"]["premieres"]}
    earliest = now + timedelta(hours=float(cfg["channel"]["publish_delay_hours"]))
    for add in range(0, 60):
        d = (now + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if d.weekday() in days and d >= earliest:
            iso = d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if iso not in taken:
                return d
    raise StepError("Could not find a free premiere slot in the next 60 days - check channel.premieres in config.yaml")


def utc_iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def engine_name(cfg: dict) -> str:
    m = str(cfg["music"].get("model", ""))
    if str(cfg["music"].get("engine", "acestep")).lower() == "acestep":
        return "ACE-Step 1.5" + (" XL" if m == "xl-turbo" else "") + " (open-source, run locally)"
    return "Stable Audio 3 " + ("Medium" if m == "medium" else "Small")


def channel_url(cfg: dict, paths) -> str:
    """Link to the channel: configured handle (nice case) > handle of the logged-in channel > channel id."""
    from ..common import channel as ch
    h = (cfg["channel"].get("expected_handle") or "").strip()
    info = ch.current(paths) if paths is not None else {}
    if not h or h.upper().startswith("UC"):
        h = info.get("handle") or ""
    if h:
        return "https://www.youtube.com/" + (h if h.startswith("@") else "@" + h)
    return f"https://www.youtube.com/channel/{info['id']}" if info.get("id") else ""


def subscribe_when(cfg: dict) -> str:
    """'every Wednesday & Monday' for fixed premieres; 'every week' when videos go public right after a manual run."""
    if cfg["channel"].get("publish_mode") == "public":
        return "every week"
    return "every " + " & ".join(DAY_NAMES.get(d.lower(), d) for d in cfg["channel"]["premieres"])


def description(cfg: dict, b: dict, extra: str = "", links: dict | None = None) -> str:
    """Listener-first description: track info, what it is good for, subscribe + more tracks, short AI note, hashtags."""
    links = links or {}
    fam = b.get("style_family", "")
    days = subscribe_when(cfg)
    from .. import seo
    lead = b.get("seo_phrase") or seo.phrase(b["substyle"])
    # first ~150 characters are shown in search results -> search phrase + track name first
    lines = [f"{FAMILY_EMOJI.get(fam, '🎵')} {lead} – {b.get('base_title') or b['title']}", "",
             b["description_story"], ""]
    if USE_CASES.get(fam):
        lines.append(f"✨ Perfect for: {USE_CASES[fam]}")
    lines += [f"🎹 Mood: {b['mood']} • 🎼 Key: {b.get('key', '-')} • 🥁 {b['bpm']} BPM", ""]
    if extra:
        lines += [extra, ""]
    if links.get("channel"):
        lines.append(f"🔔 Subscribe for new music {days}: {links['channel']}?sub_confirmation=1")
    if links.get("playlist"):
        lines.append(f"🎧 Playlist – {links.get('playlist_title') or 'more tracks'}: {links['playlist']}")
    prev = links.get("previous") or []
    if prev:
        lines.append("▶️ Previous tracks:")
        lines += [f"   • {t}: {u}" for t, u in prev]
    if links.get("channel") or links.get("playlist") or prev:
        lines.append("")
    if cfg["channel"].get("disclosure_line"):
        lines += [cfg["channel"]["disclosure_line"], ""]
    lines.append(" ".join(b["hashtags"]))
    return "\n".join(lines)[:4900]


def family_playlist(ctx: Ctx, family: str) -> tuple[str, str]:
    """Search-friendly playlist of a style family (old 'AI ... Music' playlists are renamed, not duplicated)."""
    from .. import seo
    title, old, desc = seo.PLAYLIST.get(family, (f"{family.title()} Music to Relax & Focus", None, "Instrumental music."))
    return ctx.yt.ensure_playlist(title, desc, ctx.paths.state / "playlists.json", old_title=old), title


def gather_links(ctx: Ctx, brief: dict) -> dict:
    """Channel link, playlist of this style (created if needed) and the last 2 published tracks."""
    cfg = ctx.cfg
    links: dict = {"channel": channel_url(cfg, ctx.paths), "playlist": None, "playlist_id": None, "previous": []}
    if cfg["channel"].get("playlists"):
        try:
            pid, ptitle = family_playlist(ctx, brief["style_family"])
            links.update(playlist_id=pid, playlist=f"https://www.youtube.com/playlist?list={pid}", playlist_title=ptitle)
        except StepError as e:
            ctx.note(f"Playlist not available: {e}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    done = [h for h in load_history(ctx.paths) if h.get("url") and (h.get("publish_at") or "") <= now]
    links["previous"] = [(h.get("base_title") or h.get("title") or "Track", h["url"]) for h in done[-2:]][::-1]
    return links


SEARCH_TAGS = {  # phrases people type into YouTube search + genre words, per style family (only relevant ones -
    # YouTube treats unrelated tags as spam)
    "chill": ["lofi beats to relax", "music to study to", "beats to study to", "lofi hip hop", "chill beats", "lofi",
              "chillstep", "jazzhop", "chill music"],
    "electronic": ["night drive music", "music for work", "focus music", "electronic music", "synthwave", "deep house",
                   "coding music", "gaming music"],
    "classical": ["music for studying", "classical music for reading", "relaxing piano", "piano music", "classical music",
                  "calm music", "study music"],
    "cinematic": ["epic music", "cinematic music", "motivational music", "music for writing", "emotional music",
                  "soundtrack", "orchestral music"],
    "ambient": ["music for sleep", "sleep music", "meditation music", "ambient music", "deep focus music",
                "relaxing sounds"],
    "acoustic": ["coffee shop music", "acoustic guitar music", "morning music", "cafe music", "jazz",
                 "acoustic music"],
}
# general tags valid for every track (config channel.default_tags overrides)
DEFAULT_TAGS = ["music to relax", "relaxing music", "study music", "music to study to", "focus music", "music for work",
                "instrumental music", "background music", "calm music"]


def tags(b: dict, cfg: dict | None = None) -> list[str]:
    """Video tags (YouTube limit 500 characters; multi-word tags count with their quotes).
    Order: this track (hashtags, style, mood) -> search phrases of the style family -> channel defaults."""
    defaults = list((cfg or {}).get("channel", {}).get("default_tags") or DEFAULT_TAGS)
    if (cfg or {}).get("channel", {}).get("channel_tag"):  # channel name as a tag - kept in the local config.yaml
        defaults.append(str(cfg["channel"]["channel_tag"]))
    cand = ([h.lstrip("#") for h in b["hashtags"]] + [b["substyle"], b["mood"]]
            + SEARCH_TAGS.get(b.get("style_family", ""), []) + list(defaults))
    out, total = [], 0
    for t in cand:
        t = str(t).strip()
        cost = len(t) + (2 if " " in t else 0) + 1
        if t and t.lower() not in [o.lower() for o in out] and total + cost <= 490:
            out.append(t)
            total += cost
    return out


def localizations(ctx: Ctx, brief: dict, desc_en: str, links: dict) -> dict:
    """YouTube 'localizations' (title + description per language) from the local model's translations."""
    from ..common import translate
    from .. import seo
    fam = brief.get("style_family", "")
    perfect = USE_CASES.get(fam, "")
    out = {}
    try:
        tr = translate.localize(ctx, brief, perfect)
    except Exception as e:  # noqa: BLE001 - translations must never stop an upload
        ctx.note(f"Translations skipped: {e}")
        return {}
    for lang, t in tr.items():
        b2 = {**brief, "seo_phrase": t["phrase"], "description_story": t["story"]}
        d = description(ctx.cfg, b2, links=links)
        if perfect and t.get("perfect_for"):
            d = d.replace(f"✨ Perfect for: {perfect}", f"✨ {t['perfect_for']}")
        title = f"{t['phrase']} – {brief['base_title']}{ctx.cfg['channel']['title_suffix']}"
        if len(title) > 100:
            title = seo.title(brief["substyle"], brief["base_title"], ctx.cfg["channel"]["title_suffix"],
                              lead=brief.get("seo_phrase"))
        out[lang] = {"title": title[:100], "description": d[:4900]}
    return out


def body(cfg: dict, title: str, desc: str, tag_list: list[str], publish_at: datetime | None,
         loc: dict | None = None) -> dict:
    mode = cfg["channel"]["publish_mode"]
    status = {"selfDeclaredMadeForKids": False, "containsSyntheticMedia": bool(cfg["channel"]["contains_synthetic_media"]),
              "embeddable": True, "license": "youtube"}
    if mode == "public":
        status["privacyStatus"] = "public"
    else:
        status["privacyStatus"] = "private"
        if mode == "scheduled" and publish_at is not None:
            status["publishAt"] = utc_iso(publish_at)
    out = {"snippet": {"title": title[:100], "description": desc, "tags": tag_list, "categoryId": str(cfg["channel"]["category_id"]),
                       "defaultLanguage": "en"}, "status": status}
    if loc:
        out["localizations"] = loc
    return out


def run(ctx: Ctx, brief: dict) -> dict:
    out = ctx.out_dir / "upload.json"
    up = read_json(out, {}) or {}
    if ctx.dry_run:
        ctx.log.info("Dry run - upload skipped. Files are in %s", ctx.out_dir)
        return up if up.get("video_id") else {"skipped": True, "reason": "dry-run"}  # never overwrite a real upload record
    if ctx.yt is None:
        raise StepError("No YouTube access - put client_secret.json in the secrets folder and run: AIMusicAgent.exe --auth")
    cfg = ctx.cfg
    taken = {h.get("publish_at") for h in load_history(ctx.paths) if h.get("publish_at")}

    if not up.get("video_id"):
        from ..common import channel as ch
        ch.verify(ctx.yt, cfg, ctx.paths, ctx.log)  # wrong channel -> StepError, nothing uploaded
        taken = {h.get("publish_at") for h in load_history(ctx.paths) if h.get("publish_at")}  # history may be archived
        slot = next_slot(cfg, taken)
        links = gather_links(ctx, brief)
        up["playlist_id"] = links.get("playlist_id")
        desc = description(cfg, brief, links=links)
        b = body(cfg, brief["title"], desc, tags(brief, cfg), slot, localizations(ctx, brief, desc, links))
        ctx.log.info("Uploading video: %s (%s, publish %s)", brief["title"], b["status"]["privacyStatus"],
                     b["status"].get("publishAt", "-"))
        res = ctx.yt.upload_video(ctx.out_dir / "video.mp4", b)
        up = {"video_id": res["id"], "url": f"https://youtu.be/{res['id']}", "privacy": b["status"]["privacyStatus"],
              "publish_at": b["status"].get("publishAt"), "uploaded_at": utc_iso(datetime.now(timezone.utc)),
              "playlist_id": links.get("playlist_id"), "channel_url": links.get("channel")}
        write_json(out, up)  # saved at once - a later failure never causes a double upload
        ctx.log.info("Uploaded: %s", up["url"])

    if not up.get("thumbnail_done"):
        try:
            ctx.yt.set_thumbnail(up["video_id"], ctx.out_dir / "thumb.jpg")
            up["thumbnail_done"] = True
        except StepError as e:
            ctx.note(f"Custom thumbnail not set ({e}). Custom thumbnails need a phone-verified channel.")
            up["thumbnail_done"] = "failed"
        write_json(out, up)

    if cfg["channel"].get("playlists") and not up.get("playlist_done"):
        try:
            pid = up.get("playlist_id")
            if not pid:
                pid, _ = family_playlist(ctx, brief["style_family"])
            ctx.yt.add_to_playlist(pid, up["video_id"])
            up["playlist_done"] = True
        except StepError as e:
            ctx.note(f"Playlist not updated: {e}")
            up["playlist_done"] = "failed"
        write_json(out, up)

    if cfg["shorts"]["enabled"]:
        upload_shorts(ctx, brief, up, out)
    return up


def short_offsets(cfg: dict) -> list[int]:
    """Days after the video for Short 1, 2, 3 (config shorts.offsets_days; default [0, 0, 0] = all together)."""
    offs = cfg["shorts"].get("offsets_days")
    if not isinstance(offs, list) or not offs:
        offs = [int(cfg["shorts"].get("offset_days", 0) or 0)] * 3
    return [max(0, int(x)) for x in offs]


def upload_shorts(ctx: Ctx, brief: dict, up: dict, out) -> None:
    """Up to 3 Shorts per track (different fragments, hooks and title angles), published together with the video.
    Each one links to the full track; records go to upload.json -> shorts[] (short_id = the first one)."""
    from .. import seo
    from .visuals import short_count, short_name
    cfg = ctx.cfg
    offs = short_offsets(cfg)
    done = {s.get("n"): s for s in up.get("shorts", []) if s.get("id")}
    if up.get("short_id") and 1 not in done:  # record from an older version
        done[1] = {"n": 1, "id": up["short_id"], "publish_at": up.get("short_publish_at")}
    for i in range(short_count(cfg)):
        n = i + 1
        f = ctx.out_dir / short_name(i)
        if n in done or not file_ok(f, 50_000):
            continue
        try:
            when = None
            off = offs[min(i, len(offs) - 1)]
            if up.get("publish_at"):
                when = datetime.strptime(up["publish_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) \
                    + timedelta(days=off)
            elif off > 0 and cfg["channel"]["publish_mode"] == "public":  # 'public' mode: the video is live now, Shorts 2/3 are still scheduled for later days
                zone = tz(cfg)
                hh, mm = (int(x) for x in str(cfg["channel"]["publish_time_local"]).split(":"))
                when = (datetime.now(timezone.utc).astimezone(zone) + timedelta(days=off)).replace(
                    hour=hh, minute=mm, second=0, microsecond=0)
            s_title = seo.short_title(brief["substyle"], brief["base_title"], cfg["channel"]["title_suffix"],
                                      idx=i, family=brief.get("style_family", ""))
            s_links = {"channel": up.get("channel_url") or channel_url(cfg, ctx.paths)}
            s_desc = description(cfg, brief, extra=f"🎵 Full track ▶️ {up['url']}", links=s_links)
            sb = body(cfg, s_title, s_desc, tags(brief, cfg), when)
            if when is not None and when > datetime.now(timezone.utc) + timedelta(minutes=5) \
                    and sb["status"].get("privacyStatus") == "public":  # scheduled video (e.g. tomorrow's 2nd track)
                sb["status"].update(privacyStatus="private", publishAt=utc_iso(when))  # Shorts go live with it
            res = ctx.yt.upload_video(f, sb)
            done[n] = {"n": n, "id": res["id"], "title": s_title, "publish_at": utc_iso(when) if when else None}
            ctx.log.info("Short %d uploaded: https://youtu.be/%s (publish %s)", n, res["id"], done[n]["publish_at"] or "-")
        except StepError as e:
            ctx.note(f"Short {n} not uploaded: {e}")
            break  # e.g. daily upload limit - the next run continues
        finally:
            up["shorts"] = [done[k] for k in sorted(done)]
            if 1 in done:
                up["short_id"], up["short_publish_at"] = done[1]["id"], done[1].get("publish_at")
            write_json(out, up)
