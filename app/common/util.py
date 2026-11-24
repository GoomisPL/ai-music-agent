"""Small shared helpers: logging, JSON files, step context, errors."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

_configured = False


class StepError(RuntimeError):
    """A step failed with a message meant for a human."""


def get_logger(logs_dir: Path | None = None, name: str = "aimusic") -> logging.Logger:
    global _configured
    log = logging.getLogger(name)
    if _configured:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logs_dir / f"agent-{date.today():%Y%m%d}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        log.addHandler(fh)
    _configured = True
    return log


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write: a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def file_ok(path: Path, min_bytes: int = 1) -> bool:
    return path.exists() and path.stat().st_size >= min_bytes


@dataclass
class Ctx:
    date: date
    out_dir: Path
    cfg: dict
    paths: Any
    log: logging.Logger
    yt: Any = None
    dry_run: bool = False
    timings: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    progress: Any = None   # common.progress.Progress (bar + time estimate), None in tests of single steps

    def note(self, text: str) -> None:
        self.notes.append(text)
        self.log.warning(text)


def engine_cap(paths, model: str) -> int | None:
    """Longest audio (s) the music engine really produced in one generation - learned at runtime."""
    v = (read_json(paths.state / "engine.json", {}) or {}).get("max_seconds", {}).get(model)
    return int(v) if v else None


def learn_engine_cap(paths, model: str, seconds: int) -> None:
    f = paths.state / "engine.json"
    data = read_json(f, {}) or {}
    data.setdefault("max_seconds", {})[model] = int(seconds)
    write_json(f, data)


def engine_speed(paths, model: str) -> float | None:
    """Measured CPU seconds per 1 s of generated audio for a model (learned from real runs)."""
    v = (read_json(paths.state / "engine.json", {}) or {}).get("sec_per_audio_sec", {}).get(model)
    return float(v) if v else None


def learn_engine_speed(paths, model: str, sec_per_sec: float) -> None:
    f = paths.state / "engine.json"
    data = read_json(f, {}) or {}
    sp = data.setdefault("sec_per_audio_sec", {})
    old = sp.get(model)
    sp[model] = round(sec_per_sec if not old else 0.6 * float(old) + 0.4 * sec_per_sec, 2)  # smooth, follows a new CPU
    write_json(f, data)


def history_path(paths) -> Path:
    return paths.state / "history.json"


def load_history(paths) -> list:
    h = read_json(history_path(paths), [])
    return h if isinstance(h, list) else []


def save_history(paths, items: list) -> None:
    write_json(history_path(paths), items)
