"""Cover pictures via stable-diffusion.cpp (MIT) - local, CPU, free.

Two picture models (setup.ps1 downloads both to <workspace>\\engines\\sd.cpp\\models):
  klein   FLUX.2 [klein] 4B (Apache-2.0)  - 4B parameters, ~3x less work per step than schnell   (~5 GB)
          flux-2-klein-4b-Q4_0.gguf + Qwen3-4B-Q4_K_M.gguf (text encoder) + flux2-vae.safetensors
  schnell FLUX.1-schnell 12B (Apache-2.0) - the 2.6.0 model, slow on a laptop CPU (~15 min per picture)  (~10 GB)
          flux1-schnell-Q4_0.gguf + t5xxl-Q4_K.gguf + clip_l-Q8_0.gguf + ae.safetensors
config images.model: auto (fastest working model from the speed test, klein first) | klein | schnell
Speed test (also picks the number of CPU threads): run.py --bench-image   (Panel: "Test szybkości obrazków")
Test picture: run.py --test-image "a cat with headphones by a rainy window"
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ..common import media
from ..common.util import StepError, read_json, write_json

MODELS = {
    "klein": {"diffusion": "flux-2-klein-4b-Q4_0.gguf", "llm": "Qwen3-4B-Q4_K_M.gguf", "vae": "flux2-vae.safetensors"},
    "schnell": {"diffusion": "flux1-schnell-Q4_0.gguf", "t5xxl": "t5xxl-Q4_K.gguf", "clip_l": "clip_l-Q8_0.gguf",
                "vae": "ae.safetensors"},
}
STYLE = ("cinematic digital painting, highly detailed, soft volumetric light, depth of field, rich colors, "
         "album cover art, no text")
TIMING = re.compile(r"(loading tensors|text encod|get_learned_condition|sampling|decode|generate_image)[^\n]*?"
                    r"(?:taking|in)\s+([\d.]+)\s*(m?s)", re.I)


def folders(paths) -> tuple[Path, Path]:
    home = Path(paths.home)
    b = Path(os.environ.get("AIMUSIC_SD_BIN") or home / "engines" / "sd.cpp" / "bin")
    m = Path(os.environ.get("AIMUSIC_SD_MODELS") or home / "engines" / "sd.cpp" / "models")
    return b, m


def exe(paths) -> Path | None:
    b, _ = folders(paths)
    for name in ("sd-cli.exe", "sd.exe", "sd-cli", "sd"):
        if (b / name).exists():
            return b / name
    hits = sorted(b.glob("**/sd-cli.exe")) + sorted(b.glob("**/sd.exe")) if b.exists() else []
    return hits[0] if hits else None


def has_model(paths, model: str) -> bool:
    _, m = folders(paths)
    return all((m / f).exists() for f in MODELS[model].values())


def state(paths) -> dict:
    return read_json(Path(paths.state) / "images.json", {}) or {}


def choose(cfg: dict, paths) -> str | None:
    """Model to use: config images.model, or 'auto' = the speed test's winner, else klein, else schnell."""
    want = str(cfg.get("images", {}).get("model", "auto")).lower()
    st = state(paths)
    broken = set(st.get("broken", []))
    order = [want] if want in MODELS else [st.get("best_model"), "klein", "schnell"]
    for m in order:
        if m in MODELS and m not in broken and has_model(paths, m):
            return m
    for m in ("klein", "schnell"):  # even a model marked broken is better than nothing when it is the only one
        if want == "auto" and has_model(paths, m) and m not in broken:
            return m
    return None


def missing(paths, cfg: dict | None = None) -> list[str]:
    custom = bool((cfg or {}).get("images", {}).get("command"))
    out = [] if (custom or exe(paths)) else ["sd-cli.exe"]
    if not any(has_model(paths, m) for m in MODELS):
        _, mdir = folders(paths)
        out += [f for f in MODELS["klein"].values() if not (mdir / f).exists()]
    return out


def available(cfg: dict, paths) -> bool:
    return str(cfg.get("images", {}).get("engine", "flux")).lower() == "flux" and not missing(paths, cfg)


def threads(cfg: dict, paths) -> int:
    t = int(cfg.get("images", {}).get("threads", 0) or 0)
    if t > 0:
        return t
    t = int(state(paths).get("best_threads", 0) or 0)
    return t if t > 0 else int(os.environ.get("AIMUSIC_CPU_CORES") or os.cpu_count() or 4)


def _cmd(cfg: dict, paths, model: str, prompt: str, out: Path, w: int, h: int, seed: int, t: int, steps: int,
         fa: bool) -> list:
    ic = cfg.get("images", {})
    _, m = folders(paths)
    x = exe(paths)
    prefix = [str(c) for c in (ic.get("command") or [])]  # e.g. a wrapper; selftest uses a fake generator
    if x is None and not prefix:
        raise StepError("stable-diffusion.cpp (sd-cli.exe) not installed - run the setup (option 1)")
    f = MODELS[model]
    cmd = [*(prefix or [x]), "--diffusion-model", m / f["diffusion"], "--vae", m / f["vae"]]
    if model == "klein":
        cmd += ["--llm", m / f["llm"]]
    else:
        cmd += ["--t5xxl", m / f["t5xxl"], "--clip_l", m / f["clip_l"]]
    cmd += ["-p", prompt, "--cfg-scale", "1.0", "--sampling-method", "euler", "--steps", str(steps),
            "-W", str(w), "-H", str(h), "-s", str(seed), "-t", str(t), "-o", out]
    if fa:
        cmd.append("--diffusion-fa")  # flash attention: less memory traffic in the diffusion model
    if ic.get("vae_tiling", False):
        cmd.append("--vae-tiling")
    return cmd


def _timings(text: str) -> str:
    parts = []
    for m in TIMING.finditer(text or ""):
        sec = float(m.group(2)) / (1000 if m.group(3).lower() == "ms" else 1)
        parts.append(f"{m.group(1).lower()} {sec:.0f}s")
    return ", ".join(parts[-6:])


def run_once(cfg: dict, paths, log, model: str, prompt: str, out: Path, w: int, h: int, seed: int, t: int,
             steps: int | None = None) -> float:
    """One sd-cli call; returns seconds. Retries once without flash attention if that flag is the problem."""
    ic = cfg.get("images", {})
    steps = int(steps or ic.get("steps", 4))
    x = exe(paths)
    for fa in ([True, False] if ic.get("flash_attention", True) else [False]):
        out.unlink(missing_ok=True)
        t0 = time.time()
        p = media.run(_cmd(cfg, paths, model, prompt, out, w, h, seed, t, steps, fa), log,
                      timeout=int(ic.get("timeout_min", 40)) * 60, cwd=str(x.parent) if x else None,
                      priority="above_normal")
        secs = time.time() - t0
        if p.returncode == 0 and out.exists() and out.stat().st_size >= 10_000:
            br = _timings((p.stdout or "") + "\n" + (p.stderr or ""))
            log.info("  sd.cpp %s %dx%d, %d threads%s: %.1f min%s", model, w, h, t, ", flash-attn" if fa else "",
                     secs / 60, f" ({br})" if br else "")
            return secs
        tail = "\n".join(((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-6:])
        if fa:
            log.warning("  sd.cpp failed with --diffusion-fa, trying without: %s", tail[-300:])
            continue
        raise StepError(f"image generation failed ({model}, code {p.returncode}): {tail}")
    raise StepError("image generation failed")


def generate(cfg: dict, paths, log, prompt: str, out: Path, width: int, height: int, seed: int) -> float:
    """One picture -> out (PNG). Returns minutes. width/height multiples of 64.
    If the chosen model fails, the other installed model is tried and the failing one is marked broken."""
    ic = cfg.get("images", {})
    model = choose(cfg, paths)
    if model is None:
        raise StepError("no picture model installed - run the setup (option 1)")
    full = f"{prompt}, {ic.get('style') or STYLE}"
    t = threads(cfg, paths)
    try:
        return run_once(cfg, paths, log, model, full, out, width, height, seed, t) / 60
    except StepError as e:
        other = "schnell" if model == "klein" else "klein"
        if str(ic.get("model", "auto")).lower() != "auto" or not has_model(paths, other):
            raise
        log.warning("Picture model %s failed - switching to %s: %s", model, other, str(e)[:300])
        st = state(paths)
        st["broken"] = sorted(set(st.get("broken", [])) | {model})
        write_json(Path(paths.state) / "images.json", st)
        return run_once(cfg, paths, log, other, full, out, width, height, seed, t) / 60


def bench(cfg: dict, paths, log) -> dict:
    """Speed test: each installed model x a few thread counts on a small picture; the fastest working
    combination is saved to state/images.json and used from then on."""
    out_dir = Path(paths.output) / "bench-images"
    out_dir.mkdir(parents=True, exist_ok=True)
    cores = int(os.environ.get("AIMUSIC_CPU_CORES") or os.cpu_count() or 4)
    thread_opts = sorted({max(2, cores // 3), max(2, cores // 2), cores, int(os.cpu_count() or cores)})
    prompt = "girl with headphones by a rainy window at night, warm desk lamp, city lights, " + STYLE
    results, broken = [], []
    for model in ("klein", "schnell"):
        if not has_model(paths, model):
            print(f"  {model}: brak plikow modelu - pomijam", flush=True)
            continue
        opts = thread_opts if model == "klein" else [cores]  # schnell is slow - one try only
        for t in opts:
            f = out_dir / f"{model}-{t}t.png"
            print(f"  ... {model}, {t} watkow", flush=True)
            try:
                secs = run_once(cfg, paths, log, model, prompt, f, 512, 320, 42, t)
                results.append({"model": model, "threads": t, "seconds": round(secs, 1), "file": str(f)})
                print(f"      {secs / 60:.1f} min", flush=True)
            except StepError as e:
                print(f"      BLAD: {str(e)[:200]}", flush=True)
                broken.append(model)
                break
    st = state(paths)
    st["bench"] = results
    st["broken"] = sorted(set(broken))
    if results:
        best = min(results, key=lambda r: r["seconds"])
        st.update(best_model=best["model"], best_threads=best["threads"])
        full = best["seconds"] * (1024 * 576) / (512 * 320)
        print(f"\nNajszybciej: {best['model']}, {best['threads']} watkow. Okladka 1024x576 zajmie ok. "
              f"{full / 60:.0f} min. Obrazki testowe: {out_dir}", flush=True)
    write_json(Path(paths.state) / "images.json", st)
    return st
