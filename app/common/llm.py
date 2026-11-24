"""Local LLM via the Ollama REST API (no cloud, no API keys)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

import requests

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _host(cfg: dict) -> str:
    return cfg["llm"]["host"].rstrip("/")


def server_up(cfg: dict) -> bool:
    try:
        return requests.get(f"{_host(cfg)}/api/version", timeout=3).ok
    except requests.RequestException:
        return False


_CPU_RESTARTED = False


def _local_host(cfg: dict) -> bool:
    return (urlparse(_host(cfg)).hostname or "") in ("127.0.0.1", "localhost")


def _restart_cpu_only(paths, log) -> None:
    """Old GPUs (e.g. GTX 750 / Maxwell) crash current Ollama CUDA builds. Run the server on CPU only:
    stop any running Ollama (also the tray app started at login) and start `ollama serve` with
    CUDA_VISIBLE_DEVICES=-1 - this affects ONLY the Ollama process, not games or other programs."""
    global _CPU_RESTARTED
    if sys.platform == "win32":
        for image in ("ollama app.exe", "ollama.exe"):
            subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True, creationflags=NO_WINDOW)
        time.sleep(2)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="-1", HIP_VISIBLE_DEVICES="-1", OLLAMA_NUM_GPU="0")
    log.info("Starting Ollama server in CPU-only mode...")
    subprocess.Popen([paths.ollama_exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=NO_WINDOW, env=env)
    _CPU_RESTARTED = True


def ensure_server(cfg: dict, paths, log) -> bool:
    force_cpu = bool(cfg["llm"].get("force_cpu", True)) and bool(paths.ollama_exe) and _local_host(cfg)
    if server_up(cfg) and (not force_cpu or _CPU_RESTARTED):
        return True
    if not paths.ollama_exe:
        if server_up(cfg):
            return True
        log.warning("Ollama is not running and ollama.exe was not found.")
        return False
    try:
        if force_cpu:
            _restart_cpu_only(paths, log)
        else:
            log.info("Starting Ollama server...")
            subprocess.Popen([paths.ollama_exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
    except OSError as e:
        log.warning("Cannot start Ollama: %s", e)
        return False
    for _ in range(30):
        time.sleep(2)
        if server_up(cfg):
            return True
    log.warning("Ollama did not answer within 60 s.")
    return False


def ensure_model(cfg: dict, log) -> bool:
    model = cfg["llm"]["model"]
    try:
        tags = requests.get(f"{_host(cfg)}/api/tags", timeout=10).json().get("models", [])
        names = {t.get("name", "") for t in tags} | {t.get("model", "") for t in tags}
        if model in names or f"{model}:latest" in names:
            return True
        log.info("Pulling model %s (one-time download)...", model)
        r = requests.post(f"{_host(cfg)}/api/pull", json={"name": model, "stream": False}, timeout=7200)
        return r.ok
    except requests.RequestException as e:
        log.warning("Cannot check/pull model %s: %s", model, e)
        return False


def unload(cfg: dict, log) -> None:
    """Free the RAM used by the language model before the music engine starts (keep_alive=0)."""
    try:
        requests.post(f"{_host(cfg)}/api/generate", json={"model": cfg["llm"]["model"], "keep_alive": 0}, timeout=30)
        log.info("Language model unloaded from RAM (music engine needs the memory).")
    except requests.RequestException:
        pass


def cpu_opts(cfg: dict) -> dict:
    return {"num_gpu": 0} if cfg["llm"].get("force_cpu", True) else {}


def ask_json(cfg: dict, prompt: str, required: list[str], log, retries: int = 3) -> dict | None:
    """Ask for a JSON object; returns None when the model fails `retries` times."""
    temp = float(cfg["llm"].get("temperature", 0.8))
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            r = requests.post(
                f"{_host(cfg)}/api/generate",
                json={
                    "model": cfg["llm"]["model"], "prompt": prompt, "format": "json", "stream": False,
                    "options": {"num_ctx": 8192, **cpu_opts(cfg),
                                "temperature": max(0.2, temp - 0.2 * (attempt - 1))},
                },
                timeout=int(cfg["llm"].get("timeout_seconds", 900)),
            )
            r.raise_for_status()
            text = r.json().get("response", "")
            data = json.loads(text)
            missing = [k for k in required if k not in data]
            if missing:
                raise ValueError(f"missing keys: {missing}")
            log.info("LLM answered in %.0f s (attempt %d)", time.time() - t0, attempt)
            return data
        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
            log.warning("LLM attempt %d/%d failed: %s", attempt, retries, e)
    return None
