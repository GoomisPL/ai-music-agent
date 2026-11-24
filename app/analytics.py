"""YouTube Analytics report: which videos and styles hold viewers (Panel: "Raport YouTube", run.py --report).

Per video (last N days, default 28): views, watch time, average view duration, average % watched,
subscribers gained and - when the API returns them - thumbnail impressions and click-through rate (CTR).
Then the same per style. Saved to output/report-<date>.txt and state/analytics.json;
average % watched per substyle is also used by the style choice (styles.py).

Needs the read-only Analytics permission: after updating, click "Logowanie YouTube" once.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from .common.util import StepError, load_history, read_json, save_history, write_json

API = os.environ.get("AIMUSIC_YTA_API_BASE", "https://youtubeanalytics.googleapis.com")
BASE_METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained"
CTR_METRICS = "videoThumbnailImpressions,videoThumbnailImpressionsClickRate"


def _rows(yt, start: str, end: str, metrics: str, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 200):
        r = yt.request("GET", "/v2/reports", base=API, retries=2, params={
            "ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics,
            "dimensions": "video", "filters": "video==" + ",".join(ids[i:i + 200]), "maxResults": 200,
            # 2.7 fix: the sort metric must be one of the requested metrics (CTR query has no 'views' -> HTTP 400)
            "sort": "-" + metrics.split(",")[0]})
        data = r.json()
        cols = [c["name"] for c in data.get("columnHeaders", [])]
        for row in data.get("rows", []) or []:
            d = dict(zip(cols, row))
            out[str(d.pop("video"))] = d
    return out


TRAFFIC_PL = {"YT_SEARCH": "wyszukiwarka YouTube", "SHORTS": "kanał Shorts", "RELATED_VIDEO": "proponowane filmy",
              "SUBSCRIBER": "subskrybenci / strona główna", "BROWSE": "strona główna / przeglądanie",
              "PLAYLIST": "playlisty", "YT_CHANNEL": "strona kanału", "EXT_URL": "linki zewnętrzne",
              "NO_LINK_OTHER": "bezpośrednio / inne", "NOTIFICATION": "powiadomienia", "YT_OTHER_PAGE": "inne strony YouTube",
              "END_SCREEN": "ekran końcowy", "ANNOTATION": "karty / adnotacje", "HASHTAGS": "hashtagi",
              "SOUND_PAGE": "strona dźwięku (Shorts)"}


def _traffic(yt, start: str, end: str, ids: list[str]) -> list[tuple[str, int, float]]:
    """Views and minutes per traffic source for the given videos: [(source, views, minutes)], biggest first."""
    if not ids:
        return []
    r = yt.request("GET", "/v2/reports", base=API, retries=2, params={
        "ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": "views,estimatedMinutesWatched",
        "dimensions": "insightTrafficSourceType", "filters": "video==" + ",".join(ids[:200]), "sort": "-views"})
    data = r.json()
    cols = [c["name"] for c in data.get("columnHeaders", [])]
    rows = [dict(zip(cols, row)) for row in data.get("rows", []) or []]
    return [(str(x.get("insightTrafficSourceType")), int(x.get("views") or 0), float(x.get("estimatedMinutesWatched") or 0))
            for x in rows]


def ypp_progress(yt, videos: dict, end: date) -> dict:
    """Progress towards the YouTube Partner Program thresholds (as known in 2025 - check YouTube's page):
    1000 subscribers + 4000 public watch hours of long videos in 12 months, or 1000 subs + 10M Shorts views in 90 days."""
    out: dict = {"date": end.isoformat()}
    try:
        r = yt.request("GET", "/youtube/v3/channels", params={"part": "statistics", "mine": "true"})
        st = (r.json().get("items") or [{}])[0].get("statistics", {})
        out["subscribers"] = int(st.get("subscriberCount") or 0)
    except StepError as e:
        out["subscribers_error"] = str(e)[:120]
    long_ids = [k for k, m in videos.items() if m["kind"] in ("video", "mix")]
    short_ids = [k for k, m in videos.items() if m["kind"] == "short"]
    try:
        y = _rows(yt, (end - timedelta(days=365)).isoformat(), end.isoformat(), "estimatedMinutesWatched,views", long_ids)
        out["long_hours_365"] = round(sum(float(m.get("estimatedMinutesWatched") or 0) for m in y.values()) / 60, 1)
        out["long_views_365"] = sum(int(m.get("views") or 0) for m in y.values())
        s = _rows(yt, (end - timedelta(days=90)).isoformat(), end.isoformat(), "views", short_ids)
        out["shorts_views_90"] = sum(int(m.get("views") or 0) for m in s.values())
    except StepError as e:
        out["error"] = str(e)[:160]
    return out


def explain(yt, err: str) -> str:
    """Turn an Analytics API error into a clear instruction (the raw error is kept at the end)."""
    low = err.lower()
    granted = str((getattr(yt, "_token", {}) or {}).get("scope", ""))
    if "403" in err and ("accessnotconfigured" in low or "has not been used" in low or "service_disabled" in low
                         or "is disabled" in low):
        import re
        m = re.search(r"project[ =](\d+)", err)
        proj = f"?project={m.group(1)}" if m else ""
        return ("YouTube Analytics API is not enabled in your Google Cloud project. Open "
                f"https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com{proj} , "
                "click ENABLE (Wlacz), wait 2-5 minutes and run the report again. Details: " + err[:300])
    if granted and "yt-analytics" not in granted:
        return ("The Google login did not grant access to statistics. Click 'Logowanie YouTube' again and in the "
                "Google window TICK the box 'View YouTube Analytics reports' (Google may show it unticked). "
                "Details: " + err[:300])
    if "403" in err or "401" in err:
        return ("YouTube Analytics refused access. Most often: the 'YouTube Analytics API' is not enabled in the "
                "Google Cloud project (https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com) "
                "or the statistics box was not ticked at login. Details: " + err[:300])
    return err


def collect(yt, paths, days: int = 28) -> dict:
    hist = load_history(paths)
    mixes = read_json(paths.state / "mixes.json", []) or []
    videos: dict[str, dict] = {}
    for h in hist:
        meta = {"title": h.get("base_title") or h.get("title"), "substyle": h.get("substyle"),
                "family": h.get("style_family"), "date": h.get("date")}
        if h.get("video_id"):
            videos[h["video_id"]] = {**meta, "kind": "video"}
        for sid in h.get("short_ids") or ([h["short_id"]] if h.get("short_id") else []):
            videos[sid] = {**meta, "kind": "short"}
    for m in mixes:
        if m.get("video_id"):
            videos[m["video_id"]] = {"title": m.get("title"), "substyle": "long mix", "family": m.get("family"),
                                     "date": str(m.get("created", ""))[:10], "kind": "mix"}
    if not videos:
        raise StepError("No published videos in history yet.")
    end = date.today()
    start = end - timedelta(days=days)
    ids = list(videos)
    try:
        base = _rows(yt, start.isoformat(), end.isoformat(), BASE_METRICS, ids)
    except StepError as e:
        raise StepError(explain(yt, str(e))) from e
    ctr_note = ""
    try:
        ctr = _rows(yt, start.isoformat(), end.isoformat(), CTR_METRICS, ids)
    except StepError as e:  # not every API version/channel returns impressions
        ctr, ctr_note = {}, f"CTR/impressions not available from the API ({str(e)[:120]}) - see YouTube Studio."
    for vid, meta in videos.items():
        meta.update(base.get(vid, {}))
        meta.update(ctr.get(vid, {}))
    data = {"from": start.isoformat(), "to": end.isoformat(), "videos": videos, "ctr_note": ctr_note}
    # 2.7: where do the views come from (long videos vs Shorts) + progress towards monetization
    for kind, key in ((("video", "mix"), "traffic_long"), (("short",), "traffic_shorts")):
        try:
            data[key] = _traffic(yt, start.isoformat(), end.isoformat(), [k for k, m in videos.items() if m["kind"] in kind])
        except StepError as e:
            data[key + "_error"] = str(e)[:160]
    data["ypp"] = ypp_progress(yt, videos, end)
    return data


def _f(x, nd=1, suf=""):
    return "-" if x is None else f"{float(x):.{nd}f}{suf}"


def report_text(data: dict) -> str:
    v = data["videos"]
    lines = [f"RAPORT YOUTUBE {data['from']} - {data['to']}", ""]
    for kind, head in (("video", "FILMY"), ("mix", "LONG MIXY"), ("short", "SHORTY")):
        rows = sorted(((k, m) for k, m in v.items() if m["kind"] == kind), key=lambda km: -(km[1].get("views") or 0))
        if not rows:
            continue
        lines += [head, f"{'wysw.':>6} {'min':>6} {'sr.czas':>7} {'%obej.':>6} {'subs':>4} {'impr.':>6} {'CTR':>6}  tytul (styl)"]
        for k, m in rows:
            lines.append(f"{int(m.get('views') or 0):>6} {_f(m.get('estimatedMinutesWatched'), 0):>6} "
                         f"{_f(m.get('averageViewDuration'), 0, 's'):>7} {_f(m.get('averageViewPercentage'), 0, '%'):>6} "
                         f"{int(m.get('subscribersGained') or 0):>4} {_f(m.get('videoThumbnailImpressions'), 0):>6} "
                         f"{_f(m.get('videoThumbnailImpressionsClickRate'), 1, '%'):>6}  {m.get('title')} ({m.get('substyle')})")
        lines.append("")
    per: dict[str, list] = {}
    for m in v.values():
        if m["kind"] == "video" and m.get("averageViewPercentage") is not None:
            per.setdefault(m.get("substyle") or "?", []).append(m)
    if per:
        lines += ["STYLE (filmy): sredni % obejrzenia i wyswietlenia", ]
        for sub, ms in sorted(per.items(), key=lambda kv: -sum(x["averageViewPercentage"] for x in kv[1]) / len(kv[1])):
            pct = sum(x["averageViewPercentage"] for x in ms) / len(ms)
            views = sum(int(x.get("views") or 0) for x in ms) / len(ms)
            lines.append(f"  {sub:<20} {pct:5.0f}%   {views:6.0f} wysw./film   ({len(ms)} filmow)")
        lines.append("")
    for key, head in (("traffic_long", "SKAD WIDZOWIE - DLUGIE FILMY I MIXY"), ("traffic_shorts", "SKAD WIDZOWIE - SHORTY")):
        rows = data.get(key) or []
        if rows:
            total = sum(v for _, v, _ in rows) or 1
            lines.append(head)
            for src, views, mins in rows:
                lines.append(f"  {TRAFFIC_PL.get(src, src):<32} {views:>6} wysw. ({views / total:4.0%})  {mins:7.0f} min")
            lines.append("")
        elif data.get(key + "_error"):
            lines += [f"{head}: brak danych ({data[key + '_error']})", ""]
    y = data.get("ypp") or {}
    if y:
        lines += ["POSTEP DO MONETYZACJI (progi YPP wg stanu 2025 - sprawdz aktualne na stronie YouTube)",
                  f"  subskrypcje:                       {y.get('subscribers', '-'):>8} / 1000",
                  f"  godziny ogladania dlugich (12 mies.): {y.get('long_hours_365', '-'):>8} / 4000",
                  f"  wyswietlenia Shortow (90 dni):     {y.get('shorts_views_90', '-'):>8} / 10 000 000"]
        hist = data.get("kpi_history") or []
        if len(hist) > 1:
            lines.append("  poprzednie pomiary (data: subs | godz. dlugich | Shorty 90 dni):")
            for k in hist[-6:]:
                lines.append(f"    {k.get('date')}: {k.get('subscribers', '-')} | {k.get('long_hours_365', '-')} h | "
                             f"{k.get('shorts_views_90', '-')}")
        lines.append("")
    lines += ["Jak czytac: CTR ponizej ok. 2% = miniatura/tytul malo zachecaja; % obejrzenia ponizej ok. 30% = ludzie",
              "szybko wychodza (sprawdz poczatek utworu). Agent sam czesciej wybiera style z lepszym % obejrzenia.",
              "Jesli dlugie filmy prawie nie maja ruchu z 'kanal Shorts', w Studio dodaj Shortom 'Powiazany film'",
              "(API YouTube nie pozwala ustawic go automatycznie)."]
    if data.get("ctr_note"):
        lines += ["", data["ctr_note"]]
    return "\n".join(lines)


def save_kpi(paths, data: dict) -> None:
    """Append today's YPP progress to state/kpi.json (one entry per day) - the trend for the go/no-go decision."""
    y = {k: v for k, v in (data.get("ypp") or {}).items() if not k.endswith("error")}
    if not y:
        return
    hist = [k for k in (read_json(paths.state / "kpi.json", []) or []) if k.get("date") != y.get("date")]
    hist.append(y)
    write_json(paths.state / "kpi.json", hist)
    data["kpi_history"] = hist


def feed_history(paths, data: dict) -> None:
    """Store average % watched per video in history.json (used by styles.choose)."""
    hist = load_history(paths)
    for h in hist:
        m = data["videos"].get(h.get("video_id") or "")
        if m and m.get("averageViewPercentage") is not None:
            h["avg_view_pct"] = round(float(m["averageViewPercentage"]), 1)
            if m.get("videoThumbnailImpressionsClickRate") is not None:
                h["ctr_pct"] = round(float(m["videoThumbnailImpressionsClickRate"]), 2)
    save_history(paths, hist)


def main(cfg: dict, paths, log, days: int = 28) -> int:
    from .common.yt import YouTube
    yt = YouTube(paths.secrets, log)
    data = collect(yt, paths, days)
    save_kpi(paths, data)
    write_json(paths.state / "analytics.json", data)
    feed_history(paths, data)
    text = report_text(data)
    out = paths.output / f"report-{date.today():%Y%m%d}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text, flush=True)
    print(f"\nZapisano: {out}", flush=True)
    return 0
