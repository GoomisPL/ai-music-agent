"""Channel statistics (feeds the next brief) and housekeeping (archive, disk space)."""
from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta, timezone

from ..common.util import Ctx, load_history, save_history


def update_stats(ctx: Ctx) -> None:
    if ctx.yt is None or not ctx.yt.has_token():
        return
    hist = load_history(ctx.paths)
    ids = [h["video_id"] for h in hist if h.get("video_id")]
    if not ids:
        return
    try:
        stats = ctx.yt.video_stats(ids)
    except Exception as e:
        ctx.note(f"Statistics not updated: {e}")
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for h in hist:
        s = stats.get(h.get("video_id", ""))
        if s:
            h["views"] = int(s.get("viewCount", 0) or 0)
            h["likes"] = int(s.get("likeCount", 0) or 0)
            h["comments"] = int(s.get("commentCount", 0) or 0)
            h["privacy_now"] = s.get("privacyStatus")
            h["stats_at"] = now
    save_history(ctx.paths, hist)
    total = sum(h.get("views") or 0 for h in hist)
    ctx.log.info("Statistics: %d videos, %d views in total", len(ids), total)


def housekeeping(ctx: Ctx) -> None:
    hk = ctx.cfg["housekeeping"]
    limit = date.today() - timedelta(days=int(hk["keep_output_days"]))
    for d in sorted(ctx.paths.output.iterdir()) if ctx.paths.output.exists() else []:
        try:
            day = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < limit:
            target = ctx.paths.archive / d.name
            shutil.rmtree(d / "variants", ignore_errors=True)
            shutil.move(str(d), str(target))
            ctx.log.info("Archived %s", d.name)
    for d in ctx.paths.output.glob("*/variants") if ctx.paths.output.exists() else []:
        if d.parent != ctx.out_dir:
            shutil.rmtree(d, ignore_errors=True)  # raw variants of older runs are not needed after upload
    log_limit = datetime.now().timestamp() - int(hk["keep_log_days"]) * 86400
    for f in ctx.paths.logs.glob("*.log"):
        if f.stat().st_mtime < log_limit:
            f.unlink(missing_ok=True)
    free_gb = shutil.disk_usage(ctx.paths.home).free / 1e9
    if free_gb < float(hk["min_free_gb"]):
        ctx.note(f"Low disk space: {free_gb:.1f} GB free on the workspace drive.")
