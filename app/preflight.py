"""Pre-flight check before every run: is all required software in place?

When something is missing the user gets a clear list: WHAT to download, FROM WHERE and WHAT TO DO with it.
The list is also saved as DO-POBRANIA.txt in the workspace.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .common import llm
from .common.yt import YouTube


def _item(name: str, url: str, todo: str, blocking: bool) -> dict:
    return {"name": name, "url": url, "todo": todo, "blocking": blocking}


def collect(cfg: dict, paths, log, dry_run: bool) -> list[dict]:
    missing: list[dict] = []
    ws = paths.home
    ff_ok = bool(shutil.which(paths.ffmpeg)) or Path(paths.ffmpeg).exists()
    if not ff_ok:
        missing.append(_item(
            "FFmpeg", "https://www.gyan.dev/ffmpeg/builds/  (plik: ffmpeg-release-essentials.zip)",
            f"Wrzuc ZIP do folderu installers\\ obok setup.ps1 i uruchom Uruchom-Setup.cmd (opcja 4) "
            f"ALBO rozpakuj tak, aby istnial {ws}\\tools\\ffmpeg\\bin\\ffmpeg.exe", True))
    if str(cfg["music"].get("engine", "acestep")).lower() == "acestep":
        from .common.config import resolve_model
        from .engines import acestep
        miss = acestep.missing_files(cfg, paths, resolve_model(cfg, paths))
        if miss:
            missing.append(_item(
                "ACE-Step 1.5 - silnik muzyczny (acestep.cpp + modele)",
                "https://github.com/ServeurpersoCom/acestep.cpp  |  modele: https://huggingface.co/Serveurperso/ACE-Step-1.5-GGUF",
                "Brakuje: " + ", ".join(miss) + ". Uruchom Uruchom-Setup.cmd (opcja 4) - zbuduje silnik i pobierze modele "
                f"do {paths.ace_models}. Bez GitHuba/HuggingFace: Przygotuj-Instalatory.cmd na innym komputerze.", True))
    elif not (paths.sa3_exe and Path(paths.sa3_exe).exists()):
        missing.append(_item(
            "sa3.cpp - silnik muzyczny (Stable Audio 3)", "https://github.com/betweentwomidnights/sa3.cpp",
            "Na komputerze z dostepem do GitHuba uruchom Przygotuj-Instalatory.cmd -> powstanie installers\\sa3.cpp.zip; "
            "potem tutaj Uruchom-Setup.cmd (opcja 4). Wymaga tez Visual Studio 2022 Build Tools: https://aka.ms/vs/17/release/vs_BuildTools.exe", True))
    if not paths.ollama_exe and not llm.server_up(cfg):
        missing.append(_item(
            "Ollama (lokalny model jezykowy)", "https://ollama.com/download/windows  (plik: OllamaSetup.exe)",
            "Zainstaluj i uruchom ponownie. Bez Ollamy agent dziala na zapasowych opisach (gorsza jakosc).", False))
    elif llm.ensure_server(cfg, paths, log) and not llm.ensure_model(cfg, log):
        missing.append(_item(
            f"Model jezykowy {cfg['llm']['model']}", "https://ollama.com/library/qwen2.5",
            f"W wierszu polecen: ollama pull {cfg['llm']['model']}", False))
    yt = YouTube(paths.secrets, log)
    if not yt.client_file.exists():
        missing.append(_item(
            "client_secret.json (dostep do YouTube API)", "https://console.cloud.google.com",
            f"Instrukcja krok po kroku: {paths.secrets}\\README.txt", not dry_run))
    elif not yt.has_token():
        missing.append(_item(
            "Logowanie do YouTube", "-", "Uruchom skrot 'AI Music Agent - logowanie YouTube' (AIMusicAgent.exe --auth)", not dry_run))
    return missing


def report(missing: list[dict], paths) -> str:
    lines = ["", "!" * 72, "  BRAKUJE OPROGRAMOWANIA - pobierz recznie i zainstaluj przed kolejnym uruchomieniem", "!" * 72]
    for i, m in enumerate(missing, 1):
        tag = "WYMAGANE" if m["blocking"] else "zalecane"
        lines += [f"  {i}. {m['name']}  [{tag}]", f"     Skad:  {m['url']}", f"     Co zrobic: {m['todo']}", ""]
    lines.append("!" * 72)
    text = "\n".join(lines)
    try:
        (paths.home / "DO-POBRANIA.txt").write_text(text.strip() + "\n", encoding="utf-8")
    except OSError:
        pass
    return text


def clear(paths) -> None:
    (paths.home / "DO-POBRANIA.txt").unlink(missing_ok=True)
