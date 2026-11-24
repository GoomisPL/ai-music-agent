"""Step 5: cover art, thumbnail and videos (Pillow + ffmpeg). Every track gets its own look."""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..common import media
from ..common.util import Ctx, StepError, file_ok, load_history, read_json, write_json

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\bahnschrift.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def font(size: int) -> ImageFont.ImageFont:
    for f in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def dhash(img: Image.Image) -> str:
    g = img.convert("L").resize((17, 16), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(16):
        for col in range(16):
            bits = (bits << 1) | (1 if px[row * 17 + col] > px[row * 17 + col + 1] else 0)
    return f"{bits:064x}"


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 256


def background(w: int, h: int, palette: list, family: str, rng: random.Random) -> Image.Image:
    c0, c1, c2 = (hex_rgb(c) for c in palette[:3])
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    split = rng.uniform(0.55, 0.7)
    for y in range(h):
        t = y / h
        col = mix(c0, c1, t / split) if t < split else mix(c1, mix(c0, (0, 0, 0), 0.4), (t - split) / (1 - split))
        d.line([(0, y), (w, y)], fill=col)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    if family == "electronic":
        cx, cy, r = w * rng.uniform(0.3, 0.7), h * 0.52, min(w, h) * rng.uniform(0.18, 0.28)
        for i in range(int(r), 0, -6):
            ld.ellipse([cx - i, cy - i, cx + i, cy + i], fill=c2 + (int(10 + 70 * (1 - i / r)),))
        horizon = int(h * 0.62)
        for k in range(1, 18):
            y = horizon + int((h - horizon) * (k / 18) ** 1.8)
            ld.line([(0, y), (w, y)], fill=c2 + (90,), width=2)
        for k in range(-20, 21):
            ld.line([(w / 2 + k * w / 40, horizon), (w / 2 + k * w / 6, h)], fill=c2 + (70,), width=2)
    elif family in ("classical", "chill", "ambient"):
        for _ in range(rng.randint(25, 45)):
            x, y, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(min(w, h) * 0.02, min(w, h) * 0.12)
            col = mix(c1, c2, rng.random())
            ld.ellipse([x - r, y - r, x + r, y + r], fill=col + (rng.randint(25, 70),))
        layer = layer.filter(ImageFilter.GaussianBlur(radius=max(4, w // 160)))
    else:  # cinematic: layered mountains
        for li in range(5):
            base = h * (0.45 + li * 0.11)
            col = mix(c2, c0, li / 5)
            pts = [(0, h)]
            amp = h * (0.12 - li * 0.015)
            phase = rng.uniform(0, 6.28)
            freq = rng.uniform(1.5, 3.5)
            for x in range(0, w + 40, 40):
                y = base - amp * (0.6 * math.sin(phase + freq * x / w * 6.28) + 0.4 * math.sin(phase * 2 + freq * 2.7 * x / w * 6.28))
                pts.append((x, y))
            pts.append((w, h))
            ld.polygon(pts, fill=col + (150 + li * 20,))
    img = Image.alpha_composite(img.convert("RGBA"), layer)
    vignette = Image.new("L", (w, h), 0)
    ImageDraw.Draw(vignette).ellipse([-w * 0.2, -h * 0.2, w * 1.2, h * 1.2], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(radius=w // 12))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    return Image.composite(img, dark, vignette).convert("RGB")


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        test = f"{cur} {wd}".strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines[:3]


def fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + center-crop a picture to exactly w x h (like CSS object-fit: cover)."""
    sc = max(w / img.width, h / img.height)
    im = img.convert("RGB").resize((max(w, round(img.width * sc)), max(h, round(img.height * sc))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h)).filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))


def _gradient(w: int, h: int, start: float, alpha: int) -> Image.Image:
    """Transparent -> dark gradient over the lower part (text stays readable on any picture)."""
    g = Image.new("L", (1, h), 0)
    y0 = int(h * start)
    for y in range(y0, h):
        g.putpixel((0, y), int(alpha * ((y - y0) / max(1, h - y0)) ** 1.4))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    shade.putalpha(g.resize((w, h)))
    return shade


def compose(w: int, h: int, brief: dict, rng: random.Random, title_scale: float, headline: str = "",
            art: Image.Image | None = None, badge: bool = False) -> Image.Image:
    if art is not None:  # generated picture that fits the track
        img = fit(art, w, h).convert("RGBA")
        img = Image.alpha_composite(img, _gradient(w, h, 0.55, 200))
        title_scale *= 0.75  # the picture is the star - smaller, elegant title
    else:
        img = background(w, h, brief["visual_theme"]["palette"], brief["style_family"], rng).convert("RGBA")
    d = ImageDraw.Draw(img)
    unit = min(w, h)
    if badge:  # optional "AI MUSIC" badge (config images.badge)
        bf = font(int(unit * 0.045))
        label = "AI MUSIC"
        tw = d.textlength(label, font=bf)
        pad = unit * 0.02
        # keep inside the safe area - the slow zoom in the video crops up to ~5% of each edge
        x1, y1 = w * 0.93 - tw - pad * 2, h * 0.07
        d.rounded_rectangle([x1, y1, x1 + tw + pad * 2, y1 + unit * 0.045 + pad * 1.6], radius=int(pad),
                            fill=(0, 0, 0, 170), outline=(255, 255, 255, 220), width=max(2, unit // 400))
        d.text((x1 + pad, y1 + pad * 0.6), label, font=bf, fill=(255, 255, 255, 255))
    # Title + subtitle
    tf = font(int(unit * 0.09 * title_scale))
    sf = font(int(unit * 0.04 * max(title_scale, 0.8)))
    lines = wrap(d, brief["base_title"], tf, int(w * 0.86))
    sub = f"{brief['substyle']} · {brief['mood']}"
    line_h = int(unit * 0.105 * title_scale)
    total = line_h * len(lines) + int(unit * 0.07 * title_scale)
    y = h - total - unit * 0.08
    if art is None:
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(shade).rectangle([0, y - unit * 0.04, w, h], fill=(0, 0, 0, 90))
        img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    for ln in lines:
        d.text((w * 0.08 + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 160))
        d.text((w * 0.08, y), ln, font=tf, fill=(255, 255, 255, 255))
        y += line_h
    d.text((w * 0.08, y + unit * 0.01), sub, font=sf, fill=(235, 235, 235, 230))
    if headline:
        # thumbnail: 2-3 big words readable on a phone (e.g. "LOFI • STUDY")
        hf = font(int(unit * (0.12 if art is not None else 0.15)))
        hl = [headline] if d.textlength(headline, font=hf) <= w * 0.84 else headline.split(" • ")[:2]
        while hf.size > unit * 0.08 and any(d.textlength(x, font=hf) > w * 0.86 for x in hl):
            hf = font(int(hf.size * 0.9))
        hy = h * (0.08 if art is not None else 0.20)
        for ln in hl:
            tw = d.textlength(ln, font=hf)
            x = (w - tw) / 2
            for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4), (5, 5)):
                d.text((x + dx, hy + dy), ln, font=hf, fill=(0, 0, 0, 200))
            d.text((x, hy), ln, font=hf, fill=(255, 236, 140, 255))
            hy += hf.size * 1.15
    return img.convert("RGB")


def make_art(ctx: Ctx, brief: dict) -> tuple[Image.Image | None, Image.Image | None]:
    """Pictures generated for this track (FLUX via stable-diffusion.cpp). None -> procedural backgrounds."""
    from ..engines import sdcpp
    ic = ctx.cfg.get("images", {})
    if not sdcpp.available(ctx.cfg, ctx.paths):
        miss = sdcpp.missing(ctx.paths, ctx.cfg)
        if str(ic.get("engine", "flux")).lower() == "flux":
            ctx.note("Picture generator not installed (" + ", ".join(miss[:3]) + ") - abstract backgrounds used. "
                     "Run the setup (option 1).")
        return None, None
    prompt = brief.get("image_prompt") or f"{brief['substyle']} music, {brief['mood']} mood, {brief['visual_theme']['motif']}"
    seed = ctx.date.toordinal() * 7 + len(brief.get("base_title", ""))
    out = []
    jobs = [("art_wide.png", tuple(ic.get("wide", [1024, 576])))]
    if str(ic.get("tall_mode", "derive")) == "generate":  # 2nd picture for Shorts = twice the time
        jobs.append(("art_tall.png", tuple(ic.get("tall", [576, 1024]))))
    for name, (w, h) in jobs:
        f = ctx.out_dir / name
        if not file_ok(f, 10_000):
            try:
                ctx.log.info("Generating picture %s (%dx%d): %s", name, w, h, prompt[:120])
                sdcpp.generate(ctx.cfg, ctx.paths, ctx.log, prompt, f, int(w), int(h), seed)
            except StepError as e:
                ctx.note(f"Picture {name} not generated ({str(e)[:200]}) - abstract background used.")
                out.append(None)
                continue
        out.append(Image.open(f).convert("RGB"))
    wide = out[0]
    tall = out[1] if len(out) > 1 else None
    if tall is None and wide is not None:
        tall = vertical_from(wide)
        tall.save(ctx.out_dir / "art_tall.png")
    return wide, tall


def vertical_from(img: Image.Image, w: int = 1080, h: int = 1920) -> Image.Image:
    """9:16 picture for Shorts from the 16:9 cover: blurred, darker copy fills the frame, the sharp picture
    sits in the middle at full width (no second generation needed)."""
    bg = fit(img, w, h).filter(ImageFilter.GaussianBlur(28))
    bg = Image.blend(bg, Image.new("RGB", (w, h), (0, 0, 0)), 0.35)
    fg_h = round(w * img.height / img.width * 1.25)  # a bit larger than full width - feels less like a letterbox
    fg = fit(img, w, min(h, fg_h))
    bg.paste(fg, (0, (h - fg.height) // 2 - h // 20))
    return bg


def make_images(ctx: Ctx, brief: dict) -> dict:
    qc = ctx.cfg["quality"]
    ic = ctx.cfg.get("images", {})
    badge = bool(ic.get("badge", False))
    old = [h.get("thumb_hash") for h in load_history(ctx.paths)[-int(qc["history_window"]):] if h.get("thumb_hash")]
    vw, vh = int(ctx.cfg["video"]["width"]), int(ctx.cfg["video"]["height"])
    wide, tall = make_art(ctx, brief)
    from .. import seo
    head = "" if str(ic.get("thumb_text", "headline")) == "none" else (brief.get("thumb_headline") or seo.headline(brief["substyle"]))
    for attempt in range(6):
        rng = random.Random(ctx.date.toordinal() * 31 + attempt)
        thumb = compose(1280, 720, brief, rng, 1.0, headline=head, art=wide, badge=badge)
        hsh = dhash(thumb)
        dist = min((hamming(hsh, o) for o in old), default=256)
        if wide is not None or dist >= int(qc["thumbnail_min_hash_distance"]) or attempt == 5:
            break
        ctx.log.info("  thumbnail too similar to an older one (distance %d) - new variant", dist)
    rng = random.Random(ctx.date.toordinal() * 31 + attempt)
    cover = compose(vw, vh, brief, rng, 1.0, art=wide, badge=badge)
    rng = random.Random(ctx.date.toordinal() * 31 + attempt)
    vertical = compose(1080, 1920, brief, rng, 1.0, art=tall, badge=badge)
    cover.save(ctx.out_dir / "cover.png")
    thumb.save(ctx.out_dir / "thumb.jpg", quality=92)
    vertical.save(ctx.out_dir / "cover_vertical.png")
    return {"thumb_hash": hsh, "thumb_distance": dist, "art": wide is not None}


def end_card(w: int, h: int, brief: dict) -> Image.Image:
    """Overlay for the last seconds: darkened picture + text at the top. The middle/bottom stays free for
    YouTube end screen elements (subscribe button + 2 videos), added in YouTube Studio ('Import from video')."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 150))
    d = ImageDraw.Draw(img)
    big, small = font(int(h * 0.075)), font(int(h * 0.038))
    lines = [("Thanks for listening", big, (255, 255, 255, 245)),
             ("Subscribe for new music  •  more tracks below", small, (230, 230, 230, 235))]
    y = h * 0.10
    for text, f, col in lines:
        tw = d.textlength(text, font=f)
        d.text(((w - tw) / 2, y), text, font=f, fill=col)
        y += f.size * 1.5
    return img


def render_video(ctx: Ctx, duration: float) -> None:
    v = ctx.cfg["video"]
    w, h, fps = int(v["width"]), int(v["height"]), int(v["fps"])
    out = ctx.out_dir / "video.mp4"
    tmp = ctx.out_dir / "video.part.mp4"
    frames = int(math.ceil(duration * fps)) + fps
    wave_h = max(80, h // 6)
    if v.get("zoom", True):
        bg = (f"[0:v]scale={int(w * 1.25)}:-2,zoompan=z='min(zoom+0.00012,1.10)':x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}[bg]")
        inputs = ["-i", str(ctx.out_dir / "cover.png")]
    else:
        bg = f"[0:v]scale={w}:{h},fps={fps}[bg]"
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", str(ctx.out_dir / "cover.png")]
    # waveform drawn at half width and scaled up = thicker, readable line; placed above the title band
    wmode = str(v.get("waveform", "bottom"))
    if wmode == "middle":  # big line through the middle (abstract backgrounds before 2.6)
        wy, alpha = int(h * 0.40), 0.9
    else:  # thin line low in the frame - the picture stays the main thing
        wave_h, alpha = max(48, h // 14), 0.5
        wy = h - wave_h - h // 100  # under the title, at the very bottom
    fc = (f"{bg};[1:a]showwaves=s={w // 2}x{wave_h}:mode=cline:draw=full:rate={fps}:colors=white@{alpha},"
          f"format=rgba,scale={w}:{wave_h}[wv];[bg][wv]overlay=0:{wy}:shortest=0,format=yuv420p[v]")
    if wmode == "off":
        fc = f"{bg};[bg]null,format=yuv420p[v]"
    extra_in: list[str] = []
    es = float(v.get("endscreen_seconds", 20) or 0)
    if es >= 5 and duration > es + 30:
        # last N seconds: end card (YouTube allows end screen elements in the last 5-20 s)
        card = ctx.out_dir / "endcard.png"
        end_card(w, h, ctx_brief(ctx)).save(card)
        extra_in = ["-loop", "1", "-framerate", str(fps), "-i", str(card)]
        start = duration - es
        fc = fc.replace(",format=yuv420p[v]", "[v0];[2:v]format=rgba,fade=t=in:st=" + f"{start:.2f}" + ":d=1.5:alpha=1[ec];"
                        "[v0][ec]overlay=0:0:shortest=1:enable='gte(t," + f"{start:.2f}" + ")',format=yuv420p[v]")
    media.ffmpeg(ctx.paths, [*inputs, "-i", str(ctx.out_dir / "track.wav"), *extra_in, "-filter_complex", fc,
                             "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", str(v["preset"]),
                             "-crf", str(v["crf"]), "-c:a", "aac", "-b:a", str(ctx.cfg.get("audio", {}).get("aac_bitrate", "384k")), "-ar", "48000", "-t", f"{duration:.2f}",
                             "-movflags", "+faststart", "-f", "mp4", str(tmp)], ctx.log, timeout=6 * 3600)
    tmp.replace(out)


def short_name(idx: int) -> str:
    return "short.mp4" if idx == 0 else f"short{idx + 1}.mp4"


def hook_card(text: str, accent: str = "#FFD54A") -> Image.Image:
    """Transparent 1080x1920 layer: one big line in the upper third (first ~2.5 s of a Short)."""
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(84)
    lines = wrap(d, text.upper(), f, 940)[:3]
    y = 330
    for ln in lines:
        tw = d.textlength(ln, font=f)
        x = (1080 - tw) / 2
        d.rounded_rectangle((x - 28, y - 14, x + tw + 28, y + f.size + 26), radius=22, fill=(0, 0, 0, 150))
        d.text((x, y), ln, font=f, fill=accent)
        y += int(f.size * 1.45)
    return img


def render_short(ctx: Ctx, quality: dict, idx: int = 0, start: int | None = None) -> None:
    """Vertical Short with motion: slow zoom + drift on the cover, live spectrum bars and waveform,
    a big hook line in the first seconds and audio that starts loud (no slow fade-in)."""
    secs = int(ctx.cfg["shorts"]["seconds"])
    if start is None:
        start = media.best_window(quality.get("energy_per_sec", []), secs)
    out = ctx.out_dir / short_name(idx)
    tmp = ctx.out_dir / (out.stem + ".part.mp4")
    brief = ctx_brief(ctx)
    from .. import seo
    hook = ctx.out_dir / f"hook{idx + 1}.png"
    hook_card(seo.short_hook(brief.get("style_family", ""), idx, brief)).save(hook)
    frames = secs * 30 + 30
    art = (ctx.out_dir / "art_tall.png").exists()  # generated picture: no line through the middle of it
    zoom = ["min(zoom+0.0007,1.18)", "if(eq(on,0),1.18,max(zoom-0.0007,1.0))", "min(zoom+0.0005,1.14)"][idx % 3]
    drift = ["iw/2-(iw/zoom/2)", "iw/2-(iw/zoom/2)+sin(on/90)*40", "iw/2-(iw/zoom/2)-on*0.08"][idx % 3]
    fc = (f"[0:v]scale=1296:-2,zoompan=z='{zoom}':x='{drift}':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30[bg];"
          f"[1:a]afade=t=in:d=0.15,afade=t=out:st={secs - 2}:d=2,asplit=3[a1][a2][a3];"
          "[a1]showwaves=s=540x260:mode=cline:draw=full:rate=30:colors=white@0.85,format=rgba,scale=1080:260[wv];"
          "[a3]showwaves=s=180x200:mode=p2p:draw=full:rate=30:colors=white@0.5,format=rgba,"
          "scale=1080:400:flags=neighbor[sf];"  # chunky bars at the bottom, moving with the music
          + ("[bg][sf]overlay=0:1250[b1];[b1]null[b2];[wv]nullsink;" if art else
             "[bg][sf]overlay=0:1480[b1];[b1][wv]overlay=0:840[b2];") +
          "[2:v]format=rgba,fade=t=out:st=2.3:d=0.6:alpha=1[hk];"
          "[b2][hk]overlay=0:0:enable='lt(t,3)':shortest=1,format=yuv420p[v]")
    media.ffmpeg(ctx.paths, ["-i", str(ctx.out_dir / "cover_vertical.png"),
                             "-ss", str(start), "-t", str(secs), "-i", str(ctx.out_dir / "track.wav"),
                             "-loop", "1", "-framerate", "30", "-t", str(secs), "-i", str(hook),
                             "-filter_complex", fc, "-map", "[v]", "-map", "[a2]", "-c:v", "libx264", "-preset", "veryfast",
                             "-crf", "23", "-c:a", "aac", "-b:a", str(ctx.cfg.get("audio", {}).get("shorts_aac_bitrate", "256k")),
                             "-ar", "48000", "-t", str(secs), "-movflags", "+faststart",
                             "-f", "mp4", str(tmp)], ctx.log, timeout=3600)
    tmp.replace(out)


def short_count(cfg: dict) -> int:
    return max(1, min(3, int(cfg["shorts"].get("count", 3) or 1)))


def ctx_brief(ctx: Ctx) -> dict:
    return read_json(ctx.out_dir / "brief.json", {}) or {}


def run(ctx: Ctx, brief: dict, quality: dict) -> dict:
    out = ctx.out_dir / "visuals.json"
    done = read_json(out, {}) or {}
    if not (file_ok(ctx.out_dir / "cover.png") and file_ok(ctx.out_dir / "thumb.jpg") and done.get("thumb_hash")):
        done.update(make_images(ctx, brief))
        write_json(out, done)
        ctx.log.info("Images ready (thumbnail distance to history: %s)", done["thumb_distance"])
    if not file_ok(ctx.out_dir / "video.mp4", 100_000):
        dur = media.duration(ctx.paths, ctx.out_dir / "track.wav", ctx.log)
        ctx.log.info("Rendering video (%.0f s of audio)...", dur)
        import time
        t0 = time.time()
        render_video(ctx, dur)
        ctx.timings["video_render_min"] = round((time.time() - t0) / 60, 1)
    if ctx.cfg["shorts"]["enabled"]:
        n = short_count(ctx.cfg)
        starts = media.windows(quality.get("energy_per_sec", []), int(ctx.cfg["shorts"]["seconds"]), n)
        for i, st in enumerate(starts[:n]):
            if not file_ok(ctx.out_dir / short_name(i), 50_000):
                ctx.log.info("Rendering Short %d/%d (from %d s)...", i + 1, len(starts[:n]), st)
                render_short(ctx, quality, i, st)
    if not file_ok(ctx.out_dir / "video.mp4", 100_000):
        raise StepError("video.mp4 was not created")
    return done
