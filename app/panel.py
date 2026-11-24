"""AI Music Agent - logic behind the desktop window (app/gui.py). No network server, no browser.

- Job: starts the same commands as the desktop shortcuts (run, dry run, check, YouTube login, selftest, repair)
  and collects their output for the live log,
- status(): data for the window (next premiere, model, speed, last runs, style statistics, settings),
- save_settings(): validates and writes the most important settings into config/config.yaml (backup config.yaml.bak).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from app import styles  # noqa: E402
from app.common import config  # noqa: E402
from app.common.util import load_history, read_json  # noqa: E402

APP = Path(__file__).resolve().parent
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ACTIONS = {
    "full": ([], "Pełny przebieg"),
    "dry": (["--dry-run"], "Test bez uploadu"),
    "check": (["--check"], "Sprawdzenie"),
    "auth": (["--auth"], "Logowanie YouTube"),
    "selftest": (["--selftest"], "Selftest"),
    "repair": (["--repair"], "Naprawa"),
    "abtest": (["--ab-test"], "Test jakości A/B"),
    "report": (["--report"], "Raport YouTube"),
    "imgbench": (["--bench-image"], "Test szybkości obrazków"),
    "longmix": (["--long-mix"], "Long Mix"),
}


def python_exe() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":  # child must be the console interpreter to stream output
        alt = exe.with_name("python.exe")
        if alt.exists():
            return str(alt)
    return str(exe)


class Job:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.name = ""
        self.started = None
        self.lines: deque = deque(maxlen=4000)
        self.offset = 0  # number of lines dropped from the front
        self.exit_code = None
        self.lock = threading.Lock()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def add(self, line: str) -> None:
        with self.lock:
            if len(self.lines) == self.lines.maxlen:
                self.offset += 1
            self.lines.append(line.rstrip("\r\n"))

    def start(self, key: str, extra: list[str] | None = None) -> str:
        if self.running():
            return "Już coś działa – poczekaj albo zatrzymaj."
        args, label = ACTIONS[key]
        args = [*args, *(extra or [])]
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        cmd = [python_exe(), str(APP / "run.py"), *args]
        self.name, self.started, self.exit_code = label, time.time(), None
        self.add(f"===== {datetime.now():%H:%M:%S}  {label} =====")
        self.proc = subprocess.Popen(cmd, cwd=str(APP.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                     creationflags=NO_WINDOW)
        threading.Thread(target=self._pump, daemon=True).start()
        return ""

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        _keep_awake(True)  # per-thread flag: this thread lives exactly as long as the job
        try:
            for line in self.proc.stdout:
                self.add(line)
            self.exit_code = self.proc.wait()
        finally:
            _keep_awake(False)
        self.add(f"===== koniec: kod {self.exit_code} ({(time.time() - self.started) / 60:.1f} min) =====")

    def stop(self) -> None:
        if self.running():
            assert self.proc
            if sys.platform == "win32":  # stop the whole tree (sa3, ffmpeg) - not just python
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True, creationflags=NO_WINDOW)
            else:
                self.proc.terminate()
            self.add("===== zatrzymano ręcznie =====")
            lock = config.paths().state / "run.lock"
            lock.unlink(missing_ok=True)

    def since(self, n: int) -> tuple[int, list[str]]:
        with self.lock:
            start = max(0, n - self.offset)
            return self.offset + len(self.lines), list(self.lines)[start:]


JOB = Job()


def _keep_awake(on: bool) -> None:
    """Do not let Windows go to sleep while a job runs (the same as AIMusicAgent.exe does)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ data for the page
def progress() -> dict | None:
    """Current run progress (state/progress.json) - also for runs started by the Task Scheduler."""
    d = read_json(config.paths().state / "progress.json", None)
    if not isinstance(d, dict):
        return None
    age = time.time() - float(d.get("updated_ts") or 0)
    if not d.get("finished") and age > 15 * 60:  # writer gone (crash / killed) - stale
        d["stale"] = True
    return d


def _mix_inventory(p) -> dict:
    try:
        from app import longmix
        return longmix.inventory(p)
    except Exception:  # noqa: BLE001
        return {}


def config_file() -> Path:
    return Path(os.environ.get("AIMUSIC_CONFIG") or (config.home() / "config" / "config.yaml"))


def status() -> dict:
    p = config.paths()
    cfg = config.load()
    hist = load_history(p)
    runs = []
    for f in sorted(p.output.glob("*/run-summary.json"), reverse=True)[:12]:
        s = read_json(f, {}) or {}
        up = s.get("upload") or {}
        runs.append({"date": s.get("date"), "title": s.get("title"), "style": s.get("substyle"),
                     "minutes": s.get("minutes"), "ok": s.get("exit_code") == 0, "error": s.get("error"),
                     "dry": s.get("dry_run"), "url": up.get("url"), "publish_at": up.get("publish_at"),
                     "folder": str(f.parent)})
    views = {h.get("substyle"): [] for h in hist}
    for h in hist:
        if h.get("views") is not None:
            views[h.get("substyle")].append(int(h["views"]))
    cache = read_json(p.state / "trends-cache.json", {}) or {}
    excl = [str(x).lower() for x in (cfg["styles"].get("exclude") or [])]
    cat = []
    for fam, sub, spec in styles.all_styles({"styles": {"catalog": cfg["styles"].get("catalog") or {}}}):
        used = sum(1 for h in hist if h.get("substyle") == sub)
        v = views.get(sub) or []
        cat.append({"family": fam, "substyle": sub, "demand": (cache.get(sub) or {}).get("median_views"),
                    "uses": used, "avg_views": round(sum(v) / len(v)) if v else None,
                    "excluded": sub in excl or fam in excl, "bpm": list(spec.get("bpm", ())), "length": list(spec.get("length", ()))})
    rep = read_json(p.home / "setup-report.json", {}) or {}
    bench = rep.get("benchmarks", {}) or {}
    try:
        from app.steps.upload import next_slot
        taken = {h.get("publish_at") for h in hist if h.get("publish_at")}
        d = next_slot(cfg, taken)
        slot = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."][d.weekday()] + d.strftime(" %d.%m, %H:%M")
    except Exception as e:  # noqa: BLE001
        slot = f"? ({e})"
    lock = p.state / "run.lock"
    return {
        "workspace": str(p.home),
        "job": {"running": JOB.running(), "name": JOB.name, "exit_code": JOB.exit_code,
                "elapsed_min": round((time.time() - JOB.started) / 60, 1) if JOB.started else None},
        "external_run": lock.exists() and not JOB.running(),
        "next_slot": slot,
        "model": config.resolve_model(cfg, p),
        "model_setting": cfg["music"]["model"],
        "engine": (read_json(p.state / "engine.json", {}) or {}).get("max_seconds", {}),
        "speed": (read_json(p.state / "engine.json", {}) or {}).get("sec_per_audio_sec", {}),
        "medium_installed": config.has_model_files(p, "medium", str(cfg["music"].get("encoding") or "f32")),
        "engine": config.engine(cfg),
        "installed": {m: config.has_model_files(p, m, str(cfg["music"].get("encoding") or "f32"), cfg)
                      for m in config.model_choices(cfg) if m != "auto"},
        "bench": bench,
        "free_gb": round(shutil.disk_usage(p.home).free / 1e9, 1),
        "youtube_login": (p.secrets / "token.json").exists(),
        "auth": read_json(p.state / "auth.json", {}) or {},  # 2.7: ok / expired + dates (written by app/common/yt.py)
        "channel": read_json(p.state / "channel.json", {}) or {},
        "client_secret": (p.secrets / "client_secret.json").exists(),
        "todo": (p.home / "DO-POBRANIA.txt").read_text(encoding="utf-8") if (p.home / "DO-POBRANIA.txt").exists() else "",
        "runs": runs,
        "videos": len([h for h in hist if h.get("video_id")]),
        "total_views": sum(int(h.get("views") or 0) for h in hist),
        "styles": cat,
        "mix_inventory": _mix_inventory(p),
        "settings": {
            "min_seconds": cfg["music"]["min_seconds"], "max_seconds": cfg["music"]["max_seconds"],
            "model": cfg["music"]["model"], "variants": cfg["music"]["variants"],
            "publish_mode": cfg["channel"]["publish_mode"], "premieres": cfg["channel"]["premieres"],
            "publish_time_local": cfg["channel"]["publish_time_local"],
            "shorts_enabled": cfg["shorts"]["enabled"], "shorts_seconds": cfg["shorts"]["seconds"],
            "no_repeat_substyle": cfg["styles"]["no_repeat_substyle"], "max_family_streak": cfg["styles"]["max_family_streak"],
            "exclude": excl, "search_per_run": cfg["trends"]["search_per_run"],
            "denoise": cfg.get("audio", {}).get("denoise", "off"),
            "expected_handle": cfg["channel"].get("expected_handle") or "",
        },
    }


def _int(v, lo: int, hi: int, name: str) -> int:
    try:
        x = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: to nie jest liczba") from None
    if not lo <= x <= hi:
        raise ValueError(f"{name}: dozwolone {lo}-{hi}")
    return x


def save_settings(data: dict) -> str:
    """Validate and write the editable settings into config.yaml (backup in config.yaml.bak)."""
    mn = _int(data.get("min_seconds"), 60, 600, "Minimalna długość")
    mx = _int(data.get("max_seconds"), 60, 600, "Maksymalna długość")
    if mn > mx:
        raise ValueError("Minimalna długość większa niż maksymalna")
    model = str(data.get("model"))
    choices = config.model_choices(config.load())
    if model not in choices:
        raise ValueError("Model: " + ", ".join(choices))
    days = [d for d in data.get("premieres") or [] if d in DAYS]
    if not days:
        raise ValueError("Wybierz co najmniej jeden dzień premiery")
    t = str(data.get("publish_time_local", ""))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        raise ValueError("Godzina premiery w formacie GG:MM")
    mode = str(data.get("publish_mode"))
    if mode not in ("scheduled", "private", "public"):
        raise ValueError("Tryb publikacji: scheduled, private albo public")
    known = {s for _, s, _ in styles.all_styles({"styles": {}})} | set(styles.CATALOG)
    excl = [e for e in (data.get("exclude") or []) if e in known]
    if len(styles.all_styles({"styles": {"exclude": excl}})) < 4:
        raise ValueError("Zostaw co najmniej 4 style – inaczej rotacja nie ma sensu")

    f = config_file()
    cur = {}
    if f.exists():
        cur = config._migrate(yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {})
        shutil.copy2(f, f.with_name(f.name + ".bak"))
    m = cur.setdefault("music", {})
    m.update(min_seconds=mn, max_seconds=mx, model=model, variants=_int(data.get("variants"), 1, 5, "Warianty"))
    handle = str(data.get("expected_handle") or "").strip()
    if handle and not re.fullmatch(r"@?[A-Za-z0-9._-]{3,30}|UC[A-Za-z0-9_-]{22}", handle):
        raise ValueError("Kanał docelowy: @nazwa (np. @YourChannel) albo ID kanału UC...")
    if handle and not handle.startswith(("@", "UC")):
        handle = "@" + handle
    c = cur.setdefault("channel", {})
    c.update(premieres=days, publish_time_local=t, publish_mode=mode, expected_handle=handle)
    s = cur.setdefault("shorts", {})
    s.update(enabled=bool(data.get("shorts_enabled")), seconds=_int(data.get("shorts_seconds"), 15, 59, "Długość Shorta"))
    st = cur.setdefault("styles", {})
    st.update(exclude=excl, no_repeat_substyle=_int(data.get("no_repeat_substyle"), 0, 20, "Powtórka stylu"),
              max_family_streak=_int(data.get("max_family_streak"), 1, 5, "Seria rodziny"))
    dn = str(data.get("denoise", "off"))
    if dn not in ("off", "light", "strong"):
        raise ValueError("Czyszczenie szumu: off, light albo strong")
    cur.setdefault("audio", {})["denoise"] = dn
    cur.setdefault("trends", {})["search_per_run"] = _int(data.get("search_per_run"), 0, 30, "Research stylów")
    cur["config_version"] = 7
    header = ("# AI Music Agent - konfiguracja (wersja 7). Zapisane z panelu " + datetime.now().strftime("%Y-%m-%d %H:%M")
              + ".\n# Poprzednia wersja: config.yaml.bak. Opis wszystkich ustawien: README.md i app\\common\\config.py.\n")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(header + yaml.safe_dump(cur, sort_keys=False, allow_unicode=True), encoding="utf-8")
    config.load()  # raises if something is wrong
    return "Zapisano. Zmiany obowiązują od następnego uruchomienia."


def open_target(what: str) -> None:
    """Open a workspace folder (or an URL / file path) in Windows Explorer / the default program."""
    p = config.paths()
    target = {"output": p.output, "logs": p.logs, "config": config_file().parent, "secrets": p.secrets,
              "workspace": p.home}.get(what, what)
    if sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        import webbrowser
        webbrowser.open(str(target))
