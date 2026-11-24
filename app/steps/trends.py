"""Step 1: trend research - which styles are in demand right now.

Two sources (both through the YouTube Data API, using the channel login):
  1. 'most popular' music charts per region  -> which catalog styles appear in hit titles/tags,
  2. search per substyle (most viewed videos of the last N days) -> median views = demand score.
Search results are cached for `cache_days`; each run refreshes the `search_per_run` oldest entries,
so the whole catalog stays fresh without burning the daily API quota.
Without YouTube access the step falls back to cached data and our own channel statistics.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from .. import styles
from ..common.util import Ctx, file_ok, read_json, write_json


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9\- ]+", " ", text.lower()) + " "


def _median(xs: list[int]) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return float(xs[m]) if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def run(ctx: Ctx) -> dict:
    out = ctx.out_dir / "trends.json"
    if file_ok(out):
        return read_json(out)
    cfg, tc = ctx.cfg, ctx.cfg["trends"]
    now = datetime.now(timezone.utc)
    cache_file = ctx.paths.state / "trends-cache.json"
    cache: dict = read_json(cache_file, {}) or {}
    all_st = styles.all_styles(cfg)
    online = ctx.yt is not None and ctx.yt.has_token()

    # 1) charts: catalog keywords in the current most-popular music videos
    chart_hits: dict[str, float] = {}
    regions_ok: list[str] = []
    if online:
        for region in tc["regions"]:
            try:
                items = ctx.yt.most_popular(region, int(tc["max_results"]))
            except Exception as e:  # one region failing must not stop the run
                ctx.log.warning("Charts %s: %s", region, e)
                continue
            regions_ok.append(region)
            for it in items:
                sn = it.get("snippet", {})
                views = int(it.get("statistics", {}).get("viewCount", 0) or 0)
                text = _words(sn.get("title", "") + " " + " ".join(sn.get("tags", []) or []))
                for _, sub, _spec in all_st:
                    if f" {sub} " in text:
                        chart_hits[sub] = chart_hits.get(sub, 0) + math.log10(views + 10)

    # 2) search demand per substyle, refreshing the stalest cache entries first
    searched: list[str] = []
    if online:
        def age(sub: str) -> float:
            t = cache.get(sub, {}).get("checked")
            return (now - datetime.fromisoformat(t)).total_seconds() if t else 1e12
        stale = sorted((s for _, s, _ in all_st), key=age, reverse=True)
        stale = [s for s in stale if age(s) > 86400 * float(tc["cache_days"]) / 2] or stale[:2]
        after = (now - timedelta(days=int(tc["search_days"]))).strftime("%Y-%m-%dT%H:%M:%SZ")
        specs = {s: sp for _, s, sp in all_st}
        for sub in stale[: int(tc["search_per_run"])]:
            try:
                ids = ctx.yt.search_ids(specs[sub].get("query", f"{sub} instrumental"), after)
                stats = ctx.yt.video_stats(ids) if ids else {}
                views = [int(v.get("viewCount", 0) or 0) for v in stats.values()]
                cache[sub] = {"median_views": _median(views), "videos": len(views), "checked": now.isoformat(timespec="seconds")}
                searched.append(sub)
            except Exception as e:
                ctx.log.warning("Search '%s': %s", sub, e)
                if "quota" in str(e).lower():
                    ctx.note("YouTube API quota reached during trend research - using cached data for the rest.")
                    break
        write_json(cache_file, cache)

    limit = now - timedelta(days=float(tc["cache_days"]) * 3)
    demand: dict[str, float] = {}
    for _, sub, _ in all_st:
        c = cache.get(sub)
        if c and c.get("checked") and datetime.fromisoformat(c["checked"]) > limit:
            demand[sub] = float(c.get("median_views", 0))
        if sub in chart_hits:
            demand[sub] = demand.get(sub, 0) + 10 ** min(chart_hits[sub], 8) / 10  # chart presence = strong signal
    source = "youtube" if online and (regions_ok or searched) else ("cache" if demand else "offline")
    if source == "offline":
        ctx.note("Trend research offline (no YouTube access, no cache) - style choice uses variety rules and own stats.")
    data = {
        "collected_at": now.isoformat(timespec="seconds"),
        "source": source,
        "regions": regions_ok,
        "searched_now": searched,
        "chart_hits": {k: round(v, 2) for k, v in sorted(chart_hits.items(), key=lambda kv: -kv[1])},
        "substyle_demand": {k: round(v) for k, v in sorted(demand.items(), key=lambda kv: -kv[1])},
    }
    write_json(out, data)
    top = list(data["substyle_demand"].items())[:5]
    ctx.log.info("Trends (%s): searched %d styles; top demand: %s", source, len(searched),
                 ", ".join(f"{k} {v}" for k, v in top) or "-")
    return data
