"""AI Music Agent - entry point.

    python app/run.py              full run: stats -> trends -> brief -> music -> quality -> visuals -> upload
    python app/run.py --dry-run    everything except the upload (safe test)
    python app/run.py --auth       one-time Google/YouTube login
    python app/run.py --check      environment check only
    python app/run.py --redo brief also regenerate this step's output for today (brief|music|visuals|upload)

Exit codes: 0 = OK, 1 = error, 2 = skipped (another run in progress).
Uses only the local Ollama model - no cloud LLM.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common import config, llm  # noqa: E402
from app.common.util import Ctx, StepError, get_logger, load_history, save_history, write_json  # noqa: E402
from app.common.yt import YouTube  # noqa: E402
from app import preflight  # noqa: E402
from app.steps import brief as s_brief  # noqa: E402
from app.steps import maintenance, music, quality, trends, upload, visuals  # noqa: E402

REDO = {
    "brief": ["brief.json", "variants", "quality.json", "track.wav", "visuals.json", "cover.png", "thumb.jpg",
              "cover_vertical.png", "video.mp4", "short.mp4", "short2.mp4", "short3.mp4"],
    "music": ["variants", "quality.json", "track.wav", "video.mp4", "short.mp4", "short2.mp4", "short3.mp4"],
    "visuals": ["visuals.json", "cover.png", "thumb.jpg", "cover_vertical.png", "video.mp4", "short.mp4", "short2.mp4", "short3.mp4"],
    "upload": ["upload.json"],
}


def check(cfg: dict, paths, log) -> int:
    rows = []

    def row(name, ok, detail):
        rows.append((("OK  " if ok else "FAIL"), name, detail))

    ff = shutil.which(paths.ffmpeg) or (paths.ffmpeg if Path(paths.ffmpeg).exists() else None)
    row("ffmpeg", bool(ff), paths.ffmpeg)
    if config.engine(cfg) == "acestep":
        from app.engines import acestep
        model = config.resolve_model(cfg, paths)
        miss = acestep.missing_files(cfg, paths, model)
        row(f"ACE-Step 1.5 ({model})", not miss, "missing: " + ", ".join(miss) if miss else str(paths.ace_bin))
        bad = acestep.foreign_binaries(paths)
        row("ACE-Step files are 64-bit", not bad, ("32-bit: " + ", ".join(f.name for f in bad)) if bad else "OK")
    else:
        row("sa3-generate.exe", bool(paths.sa3_exe and Path(paths.sa3_exe).exists()), str(paths.sa3_exe))
    up = llm.ensure_server(cfg, paths, log)
    row("Ollama server", up, cfg["llm"]["host"])
    row(f"Ollama model {cfg['llm']['model']}", up and llm.ensure_model(cfg, log), "")
    yt = YouTube(paths.secrets, log)
    row("client_secret.json", yt.client_file.exists(), str(yt.client_file))
    row("YouTube login (token.json)", yt.has_token(), "run --auth" if not yt.has_token() else "")
    if yt.has_token():
        from app.common import channel as ch
        try:
            info = yt.my_channel()
            exp = cfg["channel"].get("expected_handle") or ""
            row("YouTube channel", ch.matches(info, exp),
                ch.label(info) + (f"  (expected {exp})" if exp and not ch.matches(info, exp) else ""))
        except Exception as e:  # noqa: BLE001
            row("YouTube channel", False, str(e)[:120])
    free = shutil.disk_usage(paths.home).free / 1e9
    row("Free disk space", free >= float(cfg["housekeeping"]["min_free_gb"]), f"{free:.1f} GB")
    print()
    for st, n, d in rows:
        print(f"  [{st}] {n:<28} {d}")
    print()
    return 0 if all(r[0].startswith("OK") for r in rows) else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Polish letters never crash an old console code page
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="AI Music Agent")
    ap.add_argument("--dry-run", "--no-upload", action="store_true", dest="dry_run")
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--date", help="run folder date YYYY-MM-DD (default today)")
    ap.add_argument("--redo", choices=sorted(REDO), help="regenerate a step for this date")
    ap.add_argument("--selftest", action="store_true", help="run the sandbox self-test")
    ap.add_argument("--repair", action="store_true", help="diagnose and try to fix the last failed run")
    ap.add_argument("--no-repair", action="store_true", help="do not start the automatic repair after a failure")
    ap.add_argument("--bench-image", action="store_true", help="picture speed test: model + CPU threads (state/images.json)")
    ap.add_argument("--test-image", metavar="PROMPT", help="generate one test picture (output/test-image.png)")
    ap.add_argument("--report", action="store_true", help="YouTube Analytics report: views, % watched, CTR per video/style")
    ap.add_argument("--ab-test", action="store_true", help="sound quality A/B test (old vs new audio settings)")
    ap.add_argument("--long-mix", action="store_true", help="1-3 h mix of published tracks (see app/longmix.py)")
    ap.add_argument("--family", default="all", help="long mix: style family or 'all'")
    ap.add_argument("--hours", type=float, default=2.0, help="long mix length in hours (1-3)")
    args = ap.parse_args()
    here = Path(__file__).resolve().parent
    if args.selftest:
        return subprocess.call([sys.executable, str(here / "selftest.py")])
    if args.repair:
        return subprocess.call([sys.executable, str(here / "repair.py")])

    paths = config.paths()
    paths.ensure()
    log = get_logger(paths.logs)
    try:
        cfg = config.load()
    except Exception as e:
        log.error("config.yaml error: %s", e)
        return 1

    if args.auth:
        try:
            yt_ = YouTube(paths.secrets, log)
            yt_.authorize()
            from app.common import channel as ch
            info = ch.after_auth(yt_, cfg, paths, log)
            print()
            print(f"  Zalogowano. Muzyka bedzie wysylana na kanal: {ch.label(info)}")
            if not cfg["channel"].get("expected_handle"):
                print("  Wskazowka: wpisz ten kanal w Panelu (Ustawienia -> Kanal docelowy), wtedy agent")
                print("  nigdy nie wysle niczego na inny kanal.")
            return 0
        except StepError as e:
            log.error("%s", e)
            return 1
    if args.check:
        return check(cfg, paths, log)
    if args.long_mix:
        from app import longmix
        lock = paths.state / "run.lock"
        if lock.exists() and time.time() - lock.stat().st_mtime < 10 * 3600:
            log.error("Another run is in progress (lock %s).", lock)
            return 2
        lock.write_text(str(os.getpid()), encoding="utf-8")
        try:
            return longmix.main(cfg, paths, log, args.family, args.hours, args.dry_run)
        except StepError as e:
            log.error("Long Mix failed: %s", e)
            return 1
        finally:
            lock.unlink(missing_ok=True)
    if args.bench_image:
        from app.engines import sdcpp
        print("Test szybkosci obrazkow (kilka-kilkanascie minut, komputer bedzie mocno pracowal)...", flush=True)
        st = sdcpp.bench(cfg, paths, log)
        return 0 if st.get("best_model") else 1
    if args.test_image:
        from app.engines import sdcpp
        try:
            out = paths.output / "test-image.png"
            paths.output.mkdir(parents=True, exist_ok=True)
            mins = sdcpp.generate(cfg, paths, log, args.test_image, out, 1024, 576, 42)
            print(f"Obrazek gotowy w {mins:.1f} min: {out}")
            return 0
        except StepError as e:
            log.error("Test picture failed: %s", e)
            return 1
    if args.report:
        from app import analytics
        try:
            return analytics.main(cfg, paths, log)
        except StepError as e:
            log.error("Report failed: %s", e)
            return 1
    if args.ab_test:
        from app import abtest
        cfg["music"]["model"] = config.resolve_model(cfg, paths)
        try:
            return abtest.main(cfg, paths, log)
        except StepError as e:
            log.error("A/B test failed: %s", e)
            return 1

    missing = preflight.collect(cfg, paths, log, args.dry_run)
    if missing:
        print(preflight.report(missing, paths))
        if any(m["blocking"] for m in missing):
            log.error("Required software or access is missing - details above and in %s", paths.home / "DO-POBRANIA.txt")
            return 1
    else:
        preflight.clear(paths)

    lock = paths.state / "run.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 10 * 3600:
        log.error("Another run is in progress (lock %s). If not, delete that file.", lock)
        return 2
    lock.write_text(str(os.getpid()), encoding="utf-8")

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    out_dir = paths.output / run_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.redo:
        for name in REDO[args.redo]:
            p = out_dir / name
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        log.info("Redo %s: previous outputs removed", args.redo)

    resolved = config.resolve_model(cfg, paths)
    if resolved != cfg["music"]["model"]:
        log.info("Music model: %s (auto)", resolved)
    cfg["music"]["model"] = resolved
    ctx = Ctx(date=run_date, out_dir=out_dir, cfg=cfg, paths=paths, log=log, dry_run=args.dry_run)
    from app.common import progress as prog
    ctx.progress = prog.Progress(paths, log, prog.plan_for(ctx), args.dry_run)
    _, eta = ctx.progress.overall()
    log.info("Estimated run time: about %s (music engine %s: %.1f s per 1 s of audio, %s)", prog.fmt_min(eta),
             cfg["music"]["model"], *prog.music_speed(paths, str(cfg["music"]["model"])))
    t_start = time.time()
    summary: dict = {"date": run_date.isoformat(), "started": datetime.now().isoformat(timespec="seconds"),
                     "dry_run": args.dry_run}
    code = 0
    try:
        log.info("=== AI Music Agent - run %s%s ===", run_date, " (DRY RUN)" if args.dry_run else "")
        free = shutil.disk_usage(paths.home).free / 1e9
        if free < 3:
            raise StepError(f"Only {free:.1f} GB free - not enough to render a video.")

        yt = YouTube(paths.secrets, log)
        if yt.client_file.exists() and yt.has_token():
            ctx.yt = yt
            if not args.dry_run:
                # 2.7: check the login NOW (1 s) - if Google expired it, the browser login opens at the start,
                # not after an hour of music generation. No need to click "Logowanie YouTube" before every run.
                from app.common import channel as ch
                old_token = dict(yt._token)
                yt.ensure_login(interactive=True)
                if yt._token.get("refresh_token") != old_token.get("refresh_token"):
                    ch.after_auth(yt, cfg, paths, log)
        else:
            ctx.note("YouTube not connected (missing client_secret.json or login) - trends offline"
                     + ("" if args.dry_run else ", upload will fail"))

        if llm.ensure_server(cfg, paths, log):
            llm.ensure_model(cfg, log)

        def step(name, fn, *a):
            log.info("--- %s ---", name)
            t0 = time.time()
            ctx.progress.start(name)
            if name.startswith("music"):
                r = fn(*a)  # music moves the bar itself (per variant / section)
            else:
                est = next((s["est"] for s in ctx.progress.steps if s["key"] == name), 60)
                with ctx.progress.timed(0, 1, est, ""):
                    r = fn(*a)
            ctx.progress.finish(name)
            ctx.timings[f"{name}_min"] = round((time.time() - t0) / 60, 1)
            return r

        step("stats", maintenance.update_stats, ctx)
        tr = step("trends", trends.run, ctx)
        br = step("brief", s_brief.run, ctx, tr)
        # now the exact track length is known -> better estimate for music and video
        for k, v in prog.plan_for(ctx, br.get("target_seconds")):
            if k in ("music", "visuals"):
                ctx.progress.set_estimate(k, v)
        _, eta = ctx.progress.overall()
        log.info("Track %s s - estimated time left: about %s", br.get("target_seconds"), prog.fmt_min(eta))
        if llm.server_up(cfg):
            llm.unload(cfg, log)
        variants = step("music", music.run, ctx, br)
        q = step("quality", quality.run, ctx, variants, br.get("target_seconds"))
        if not q.get("ok"):
            log.warning("All variants rejected - generating one more round")
            variants = step("music_retry", music.run, ctx, br, 1)
            q = step("quality_retry", quality.run, ctx, variants, br.get("target_seconds"))
            if not q.get("ok"):
                raise StepError(f"No variant passed the quality gate: {q.get('rejected')}")
        br = s_brief.apply_variant(ctx, br, q.get("chosen"))  # 2.7: the winner may be the alternative piece
        vis = step("visuals", visuals.run, ctx, br, q)
        up = step("upload", upload.run, ctx, br)

        if up.get("video_id"):
            hist = load_history(paths)
            if not any(h.get("video_id") == up["video_id"] for h in hist):
                hist.append({
                    "date": run_date.isoformat(), "video_id": up["video_id"], "url": up["url"], "title": br["title"],
                    "base_title": br["base_title"], "style_family": br["style_family"], "substyle": br["substyle"], "key": br.get("key"),
                    "seconds": round(q.get("duration") or 0),
                    "mood": br["mood"], "bpm": br["bpm"], "music_prompt": br["music_prompt"],
                    "scene": br.get("scene"), "texture": br.get("texture"),
                    "hooks": br.get("hooks"), "short_angles": br.get("short_angles"),
                    "publish_at": up.get("publish_at"), "short_id": up.get("short_id"),
                    "short_ids": [s["id"] for s in up.get("shorts", []) if s.get("id")],
                    "fingerprint": q.get("fingerprint"), "thumb_hash": vis.get("thumb_hash"), "views": None,
                })
                save_history(paths, hist)
            else:  # resumed run: Shorts 2/3 may have been uploaded now
                for h in hist:
                    if h.get("video_id") == up["video_id"]:
                        h["short_ids"] = [s["id"] for s in up.get("shorts", []) if s.get("id")] or h.get("short_ids", [])
                save_history(paths, hist)
        if q.get("bonus") and cfg.get("bonus", {}).get("enabled"):
            from app import bonus
            try:
                summary["bonus"] = step("bonus", bonus.run, ctx, br, q, up)
            except StepError as e:  # the main video is already out - never fail the run for the extra one
                ctx.note(f"Bonus video not made: {e}")
        summary.update(title=br["title"], style=br["style_family"], substyle=br["substyle"], upload=up,
                       quality={"chosen": q.get("chosen"), "rejected": len(q.get("rejected", {}))})
        step("housekeeping", maintenance.housekeeping, ctx)
    except StepError as e:
        log.error("STOPPED: %s", e)
        summary["error"] = str(e)
        code = 1
    except Exception as e:  # unexpected - keep the traceback in the log
        log.error("UNEXPECTED ERROR: %s\n%s", e, traceback.format_exc())
        summary["error"] = repr(e)
        code = 1
    finally:
        summary.update(finished=datetime.now().isoformat(timespec="seconds"), minutes=round((time.time() - t_start) / 60, 1),
                       timings=ctx.timings, notes=ctx.notes, exit_code=code)
        write_json(out_dir / "run-summary.json", summary)
        ctx.progress.end(code == 0, summary.get("error") or summary.get("title") or "")
        lock.unlink(missing_ok=True)

    print()
    print("=" * 70)
    if code == 0:
        u = summary.get("upload", {})
        print(f"  DONE in {summary['minutes']} min: {summary.get('title')}")
        if u.get("url"):
            print(f"  Video: {u['url']}  ({u.get('privacy')}, publish {u.get('publish_at') or 'now'})")
        else:
            print(f"  Files: {out_dir}")
    else:
        print(f"  FAILED after {summary['minutes']} min: {summary.get('error')}")
        print(f"  Log: {paths.logs}   Summary: {out_dir / 'run-summary.json'}")
    for n in ctx.notes:
        print(f"  note: {n}")
    print("=" * 70)

    if code == 1 and cfg.get("repair", {}).get("auto") and not args.no_repair:
        log.info("Starting automatic repair...")
        rc = subprocess.call([sys.executable, str(here / "repair.py")])
        if rc == 0:
            log.info("Repair applied a fix - running once more")
            again = [sys.executable, str(here / "run.py"), "--no-repair"]
            if args.dry_run:
                again.append("--dry-run")
            if args.date:
                again += ["--date", args.date]
            return subprocess.call(again)
    if code == 0 and not args.dry_run:
        auto_long_mix(cfg, paths, log)
        auto_report(cfg, paths, log)
    return code


def auto_report(cfg: dict, paths, log) -> None:
    """2.7: refresh the YouTube report after every successful run (output/report-<date>.txt + state/kpi.json),
    so the trend towards monetization builds up without clicking 'Raport YouTube'."""
    try:
        from app import analytics
        from app.common.yt import YouTube
        data = analytics.collect(YouTube(paths.secrets, log), paths)
        analytics.save_kpi(paths, data)
        write_json(paths.state / "analytics.json", data)
        analytics.feed_history(paths, data)
        out = paths.output / f"report-{date.today():%Y%m%d}.txt"
        out.write_text(analytics.report_text(data), encoding="utf-8")
        y = data.get("ypp") or {}
        log.info("Report: %s | subs %s/1000, long-video hours %s/4000 (12 m), Shorts views %s (90 d)", out.name,
                 y.get("subscribers", "-"), y.get("long_hours_365", "-"), y.get("shorts_views_90", "-"))
    except Exception as e:  # noqa: BLE001 - the report must never fail a finished run
        log.warning("YouTube report skipped: %s", str(e)[:200])


def auto_long_mix(cfg: dict, paths, log) -> None:
    """After a normal run: when N new published tracks (not used in any mix yet) are waiting, make a 1-hour mix
    automatically (config longmix.auto_every, 0 = off). 2.7: new tracks first, the hour is filled with older ones."""
    every = int((cfg.get("longmix") or {}).get("auto_every", 0) or 0)
    if every <= 0:
        return
    try:
        from app import longmix
        n, minutes = longmix.fresh_count(paths)
        if n < every:
            log.info("Long Mix: %d new tracks (%.0f min) - automatic 1 h mix after %d", n, minutes, every)
            return
        log.info("Long Mix: %d new tracks waiting - making a 1-hour mix (new tracks first)", n)
        longmix.main(cfg, paths, log, "all", 1.0, False, fresh_only=False)
    except Exception as e:  # noqa: BLE001 - the track itself is already done
        log.warning("Automatic Long Mix skipped: %s", e)


if __name__ == "__main__":
    sys.exit(main())
