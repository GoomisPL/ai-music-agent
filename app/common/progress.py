"""Progress bar + time estimate for a run.

- every step has an estimated duration (past runs, measured engine speed, sensible defaults),
- state/progress.json is updated every few seconds (the Panel window shows it as a progress bar,
  also when the run was started by the Task Scheduler),
- the console gets a short bar line now and then:
      [########............]  42%  Muzyka: wersja 1/2  |  zostalo ok. 14 min (koniec ok. 18:52)
"""
from __future__ import annotations

import statistics
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from .util import read_json, write_json

LABELS = {
    "stats": "Statystyki kanału", "trends": "Research trendów", "brief": "Pomysł na utwór (model językowy)",
    "music": "Muzyka", "quality": "Kontrola jakości i mastering", "visuals": "Grafika i wideo",
    "upload": "Wysyłka na YouTube", "bonus": "Drugi utwór (osobny film)", "housekeeping": "Porządki",
    "mix_audio": "Long Mix: łączenie utworów", "mix_video": "Long Mix: wideo", "mix_upload": "Long Mix: wysyłka",
}
DEFAULT_SEC = {"stats": 10, "trends": 30, "brief": 90, "quality": 60, "upload": 150, "housekeeping": 10}


def fmt_min(sec: float) -> str:
    m = max(0, round(sec / 60))
    if m >= 90:
        return f"{m // 60} h {m % 60:02d} min"
    return f"{m} min" if m >= 1 else "< 1 min"


def past_minutes(paths, key: str, last: int = 6) -> float | None:
    """Median duration of a step in the last runs (run-summary.json timings)."""
    vals = []
    for f in sorted(paths.output.glob("*/run-summary.json"), reverse=True)[:20]:
        t = (read_json(f, {}) or {}).get("timings") or {}
        v = t.get(f"{key}_min")
        if isinstance(v, (int, float)) and v > 0:
            vals.append(float(v))
        if len(vals) >= last:
            break
    return statistics.median(vals) if vals else None


def music_speed(paths, model: str) -> tuple[float, str]:
    """CPU seconds per 1 s of audio for this model and where the number comes from."""
    from .util import engine_speed
    sp = engine_speed(paths, model)
    if sp:
        return sp, "zmierzone"
    bench = (read_json(paths.home / "setup-report.json", {}) or {}).get("benchmarks", {}) or {}
    if bench.get("music_sec_per_audio_sec"):
        return float(bench["music_sec_per_audio_sec"]) * (2 if model == "xl-turbo" else 1), "test w setupie"
    return {"small-music": 4.5, "medium": 12.0, "turbo": 2.0, "xl-turbo": 4.0}.get(model, 3.0), "zgrubnie"


class Progress:
    def __init__(self, paths, log, plan: list[tuple[str, float]], dry_run: bool = False):
        self.paths, self.log = paths, log
        self.file = paths.state / "progress.json"
        self.steps = [{"key": k, "label": LABELS.get(k, k), "est": max(1.0, float(s)), "state": "todo", "took": None}
                      for k, s in plan]
        self.started = time.time()
        self.cur: dict | None = None
        self.frac = 0.0
        self.detail = ""
        self.dry_run = dry_run
        self._last_write = 0.0
        self._last_print = 0.0
        self._last_pct = -100
        self._lock = threading.Lock()
        self._write(force=True)

    # ------------------------------------------------------------ estimates
    def set_estimate(self, key: str, seconds: float) -> None:
        for s in self.steps:
            if s["key"] == key and s["state"] != "done":
                s["est"] = max(1.0, float(seconds))
        self._write(force=True)

    def _cur_est(self) -> float:
        if not self.cur:
            return 0.0
        elapsed = time.time() - self.cur["t0"]
        est = self.cur["est"]
        if elapsed > est * 0.95:  # running longer than expected - stretch the estimate instead of sitting at 99 %
            est = elapsed / 0.95
        return est

    def overall(self) -> tuple[float, float]:
        """(fraction 0..1, remaining seconds)"""
        done = sum(s["est"] for s in self.steps if s["state"] in ("done", "skipped"))
        todo = sum(s["est"] for s in self.steps if s["state"] == "todo")
        cur_est = self._cur_est()
        total = done + todo + cur_est
        cur_done = cur_est * self.frac
        remaining = todo + cur_est * (1 - self.frac)
        return (done + cur_done) / total if total else 0.0, remaining

    # ------------------------------------------------------------ steps
    def start(self, key: str, detail: str = "") -> None:
        with self._lock:
            if not any(s["key"] == key for s in self.steps):  # e.g. music_retry: insert before the remaining steps
                base = next((s for s in self.steps if s["key"] == key.replace("_retry", "")), None)
                pos = next((i for i, s in enumerate(self.steps) if s["state"] == "todo"), len(self.steps))
                self.steps.insert(pos, {"key": key, "label": LABELS.get(key.replace("_retry", ""), key) + " (ponownie)",
                                        "est": base["est"] if base else 60.0, "state": "todo", "took": None})
            for s in self.steps:
                if s["key"] == key:
                    s["state"] = "running"
                    self.cur = {"key": key, "label": s["label"], "est": s["est"], "t0": time.time()}
            self.frac, self.detail = 0.0, detail
        self._write(force=True)
        self._print(force=True)

    def update(self, frac: float, detail: str | None = None) -> None:
        with self._lock:
            self.frac = max(self.frac, min(0.99, max(0.0, frac)))
            if detail is not None:
                self.detail = detail
        self._write()
        self._print()

    def finish(self, key: str) -> None:
        with self._lock:
            for s in self.steps:
                if s["key"] == key:
                    s["state"], s["took"] = "done", round(time.time() - self.cur["t0"]) if self.cur else None
                    s["est"] = s["took"] or s["est"]
            self.cur, self.frac, self.detail = None, 0.0, ""
        self._write(force=True)

    def end(self, ok: bool, message: str = "") -> None:
        self.cur = None
        data = self._data()
        data.update(finished=True, ok=ok, overall=1.0 if ok else data["overall"], eta_sec=0, message=message)
        try:
            write_json(self.file, data)
        except OSError:
            pass

    @contextmanager
    def timed(self, base: float, span: float, expected_sec: float, detail: str):
        """While a long program runs (music engine, video render): move the bar by elapsed / expected time."""
        stop = threading.Event()
        t0 = time.time()
        self.update(base, detail)

        def beat():
            while not stop.wait(5):
                el = time.time() - t0
                part = el / expected_sec if expected_sec > 0 else 0.5
                part = part if part < 0.9 else 0.9 + 0.09 * (1 - 1 / (1 + (part - 0.9) * 5))  # slow down near the end
                self.update(base + span * min(0.99, part), detail)

        th = threading.Thread(target=beat, daemon=True)
        th.start()
        try:
            yield
        finally:
            stop.set()
            self.update(base + span, detail)

    # ------------------------------------------------------------ output
    def _data(self) -> dict:
        frac, remaining = self.overall()
        return {
            "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
            "updated": datetime.now().isoformat(timespec="seconds"),
            "updated_ts": time.time(),
            "elapsed_sec": round(time.time() - self.started),
            "step": self.cur["key"] if self.cur else None,
            "label": self.cur["label"] if self.cur else "",
            "detail": self.detail,
            "step_frac": round(self.frac, 3),
            "overall": round(frac, 3),
            "eta_sec": round(remaining),
            "eta_at": (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M"),
            "dry_run": self.dry_run,
            "finished": False,
            "steps": [{k: s[k] for k in ("key", "label", "state", "est", "took")} for s in self.steps],
        }

    def _write(self, force: bool = False) -> None:
        if not force and time.time() - self._last_write < 2:
            return
        self._last_write = time.time()
        try:
            write_json(self.file, self._data())
        except OSError:
            pass

    def _print(self, force: bool = False) -> None:
        frac, remaining = self.overall()
        pct = int(frac * 100)
        if not force and pct - self._last_pct < 5 and time.time() - self._last_print < 60:
            return
        self._last_pct, self._last_print = pct, time.time()
        n = int(frac * 20)
        what = (self.cur["label"] if self.cur else "") + (f": {self.detail}" if self.detail else "")
        end = (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M")
        print(f"  [{'#' * n}{'.' * (20 - n)}] {pct:3d}%  {what}  |  zostało ok. {fmt_min(remaining)} (koniec ok. {end})",
              flush=True)


def plan_for(ctx, target_seconds: float | None = None) -> list[tuple[str, float]]:
    """Estimated seconds per step for this run."""
    cfg, p = ctx.cfg, ctx.paths
    mc = cfg["music"]
    target = target_seconds or (int(mc["min_seconds"]) + int(mc["max_seconds"])) / 2
    speed, _ = music_speed(p, str(mc["model"]))
    music = speed * target * int(mc["variants"]) * 1.05

    def est(key, default):
        v = past_minutes(p, key)
        return v * 60 if v else default

    plan = [("stats", est("stats", DEFAULT_SEC["stats"])), ("trends", est("trends", DEFAULT_SEC["trends"])),
            ("brief", est("brief", DEFAULT_SEC["brief"])), ("music", music),
            ("quality", est("quality", DEFAULT_SEC["quality"])), ("visuals", est("visuals", target * 0.8))]
    if not ctx.dry_run:
        plan.append(("upload", est("upload", DEFAULT_SEC["upload"])))
    plan.append(("housekeeping", DEFAULT_SEC["housekeeping"]))
    return plan
