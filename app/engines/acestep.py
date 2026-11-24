"""ACE-Step 1.5 through acestep.cpp (MIT, C++/ggml, runs on CPU; Vulkan/CUDA when built with them).

One generation = one whole track (up to 600 s), 48 kHz stereo, no section stitching.
    1. ace-lm    request.json  -> request0.json   (song plan: metadata + 5 Hz audio codes)
    2. ace-synth request0.json -> request00.wav   (DiT + VAE render)
Instrumental only: lyrics = "[Instrumental]". All options: config.yaml -> acestep.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from ..common import media
from ..common.config import ace_file
from ..common.util import Ctx, StepError, engine_speed, file_ok, read_json, write_json

AUDIO_EXT = (".wav", ".mp3", ".flac")


def binaries(paths) -> dict[str, Path | None]:
    out: dict[str, Path | None] = {}
    for name in ("ace-lm", "ace-synth"):
        f = None
        if paths.ace_bin:
            for cand in (paths.ace_bin / f"{name}.exe", paths.ace_bin / name):
                if cand.exists():
                    f = cand
                    break
        out[name] = f
    return out


def gguf(name: str) -> str:
    name = str(name).strip()
    return name if name.lower().endswith(".gguf") else name + ".gguf"


def pe_machine(f: Path) -> str:
    """'x64', 'x86', 'arm64' or '?' from the PE header of an .exe/.dll."""
    try:
        with open(f, "rb") as fh:
            head = fh.read(4096)
        off = int.from_bytes(head[0x3C:0x40], "little")
        if head[:2] != b"MZ" or head[off:off + 4] != b"PE\0\0":
            return "?"
        m = int.from_bytes(head[off + 4:off + 6], "little")
        return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(m, hex(m))
    except OSError:
        return "?"


def foreign_binaries(paths) -> list[Path]:
    """32-bit (or other non-x64) .exe/.dll next to the engine - Windows shows a '32/64-bit' error box for them."""
    if not paths.ace_bin or not paths.ace_bin.exists():
        return []
    return [f for f in paths.ace_bin.iterdir()
            if f.suffix.lower() in (".exe", ".dll") and pe_machine(f) not in ("x64", "?")]


def quarantine_foreign(ctx: Ctx) -> None:
    bad = foreign_binaries(ctx.paths)
    if not bad:
        return
    q = ctx.paths.ace_bin / "_not_x64"
    q.mkdir(exist_ok=True)
    for f in bad:
        shutil.move(str(f), str(q / f.name))
    ctx.note(f"Moved {len(bad)} non-64-bit file(s) away from the music engine: {', '.join(f.name for f in bad)} -> {q}")


def missing_files(cfg: dict, paths, model: str) -> list[str]:
    """What is missing for a run (binaries and model files) - used by preflight and check."""
    miss = [f"{n}.exe" for n, f in binaries(paths).items() if f is None]
    a = cfg["acestep"]
    d = paths.ace_models
    if not d or not d.exists():
        return miss + [f"models folder {d}"]
    need = [gguf(ace_file(cfg, model))] + ([gguf(a["lm_model"])] if a.get("use_lm", True) else [])
    miss += [n for n in need if not (d / n).exists()]
    if not any(d.glob("*vae*.gguf")):
        miss.append("vae-BF16.gguf")
    if not any(d.glob("Qwen3-Embedding*.gguf")):
        miss.append("Qwen3-Embedding-0.6B-Q8_0.gguf")
    return miss


def keyscale(key: str | None) -> str:
    """'F sharp minor' -> 'F# minor', 'B flat major' -> 'Bb major' (ACE-Step notation)."""
    k = (key or "").strip()
    k = re.sub(r"\s+sharp\b", "#", k, flags=re.I)
    k = re.sub(r"\s+flat\b", "b", k, flags=re.I)
    return k


def _help(ctx: Ctx, exe: Path) -> str:
    cache = ctx.paths.state / f"{exe.stem}_help.txt"
    if not file_ok(cache):
        try:
            p = media.run([str(exe), "--help"], ctx.log, timeout=60, cwd=str(exe.parent))
            cache.write_text((p.stdout or "") + (p.stderr or ""), encoding="utf-8")
        except (StepError, OSError):
            cache.write_text("", encoding="utf-8")
    return cache.read_text(encoding="utf-8", errors="replace")


def _cmd(template: list, repl: dict, extra: list) -> list[str]:
    out = []
    for part in template:
        for k, v in repl.items():
            part = part.replace(k, v)
        out.append(part)
    return out + extra


def cpu_cores() -> int:
    """Physical cores (setup stores them in AIMUSIC_CPU_CORES); fallback: logical CPUs - 25 %."""
    try:
        n = int(os.environ.get("AIMUSIC_CPU_CORES") or 0)
    except ValueError:
        n = 0
    return n if n > 0 else max(1, int((os.cpu_count() or 4) * 0.75))


def vae_file(ctx: Ctx) -> str:
    """Decoder used to turn the model output into sound. Default: ScragVAE (fine-tuned decoder - the stock one
    dulls everything above ~6 kHz and has a much smaller dynamic range = 'low-bitrate MP3' sound)."""
    want = str(ctx.cfg["acestep"].get("vae") or "").strip()
    if not want or want.lower() in ("default", "stock", "official"):
        want = "vae-BF16"  # explicit: with several VAEs in the folder the engine would just take the first one
    name = gguf(want)
    if ctx.paths.ace_models and (ctx.paths.ace_models / name).exists():
        return name
    if not getattr(ctx, "_vae_warned", False):
        ctx.note(f"VAE {name} not found in {ctx.paths.ace_models} - using the stock decoder. "
                 "Run Uruchom-Setup.cmd (option 4) to download it.")
        setattr(ctx, "_vae_warned", True)
    return ""


def _newest_audio(folder: Path, since: float) -> Path | None:
    files = [f for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXT and f.stat().st_mtime >= since - 1]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def generate(ctx: Ctx, prompt: str, seconds: int, seed: int, out: Path, brief: dict) -> tuple[bool, str]:
    """Render one full track into `out` (32-bit float WAV, 48 kHz). Returns (ok, error text)."""
    cfg, a = ctx.cfg, ctx.cfg["acestep"]
    model = str(cfg["music"]["model"])
    bins = binaries(ctx.paths)
    if not all(bins.values()):
        return False, "acestep.cpp binaries not found (AIMUSIC_ACE_BIN). Run setup again (step 8)."
    quarantine_foreign(ctx)
    work = out.parent / f"{out.stem}_ace"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    req = {
        "caption": prompt,
        "lyrics": "[Instrumental]",
        "duration": float(seconds),
        "seed": int(seed),
        "bpm": int(brief.get("bpm") or 0),
        "keyscale": keyscale(brief.get("key")),
        "timesignature": "4",
        "inference_steps": int(a.get("steps") or 0),
        "shift": float(a.get("shift") or 0),
        # acestep.cpp looks models up by their FILE NAME, including ".gguf"
        "synth_model": gguf(ace_file(cfg, model)),
        "lm_model": gguf(a["lm_model"]),
        "use_cot_caption": bool(a.get("use_cot_caption", True)),
        "output_format": a.get("output_format", "wav32"),
        "task_type": "text2music",
        "vae": vae_file(ctx),
        # acestep.cpp normally HARD-CLIPS the loudest 0.001 % of samples (peak_clip 10) to sound louder.
        # 0 = clean peak normalisation; loudness is done by our own mastering (constant gain + true-peak limiter).
        "peak_clip": int(a.get("peak_clip", 0)),
        **(a.get("extra_request") or {}),
    }
    # 0 / "" mean "model default" - leave such keys out instead of sending them (peak_clip 0 is a real value)
    req = {k: v for k, v in req.items()
           if isinstance(v, bool) or k in ("seed", "peak_clip") or v not in (0, 0.0, "", None)}
    request = work / "request.json"
    write_json(request, req)

    # timeout grows with the measured speed of this PC (first run: generous)
    speed = engine_speed(ctx.paths, model)
    timeout = max(int(cfg["music"]["timeout_minutes"]) * 60, int(speed * seconds * 3) + 900 if speed else 4 * 3600)
    repl = {"{lm}": str(bins["ace-lm"]), "{synth}": str(bins["ace-synth"]), "{models}": str(ctx.paths.ace_models)}
    threads = int(a.get("threads") or 0) or cpu_cores()
    # acestep.cpp itself uses logical CPUs / 2 (8 of 16 on a hybrid i5 = ~50 % CPU). Our build reads ACESTEP_THREADS
    # (small patch applied by setup); default = all physical cores (P + E cores).
    run_env = {"ACESTEP_THREADS": str(threads)} if threads > 0 else None
    prio = str(a.get("priority", "above_normal"))

    def extra(exe: Path) -> list[str]:
        if threads < 0:  # -1 = never pass a thread option (set by repair when the binary rejected it)
            return []
        h = _help(ctx, exe)
        return ["--threads", str(threads)] if "--threads" in h else (["-t", str(threads)] if re.search(r"(^|\s)-t[,\s]", h) else [])

    t0 = time.time()
    synth_req = request
    if a.get("use_lm", True):
        try:
            p = media.run(_cmd(a["lm_cmd"], {**repl, "{request}": str(request)}, extra(bins["ace-lm"])),
                          ctx.log, timeout=timeout, cwd=str(work), env=run_env, priority=prio)
        except StepError as e:  # timeout / missing program -> a failed variant, not a crashed run
            return False, f"ace-lm {e}"
        planned = sorted(work.glob("request[0-9]*.json"))
        if p.returncode != 0 or not planned:
            tail = "\n".join(((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-8:])
            return False, f"ace-lm code {p.returncode}: {tail}"
        synth_req = planned[0]
        plan = read_json(synth_req, {}) or {}
        ctx.log.info("    ACE-Step plan: %s BPM, %s, %s s (LM %.1f min)", plan.get("bpm"), plan.get("keyscale"),
                     plan.get("duration"), (time.time() - t0) / 60)
    t1 = time.time()
    try:
        p = media.run(_cmd(a["synth_cmd"], {**repl, "{request}": str(synth_req)}, extra(bins["ace-synth"])),
                      ctx.log, timeout=timeout, cwd=str(work), env=run_env, priority=prio)
    except StepError as e:
        return False, f"ace-synth {e}"
    audio = _newest_audio(work, t1)
    plan_now = read_json(synth_req, {}) or {}
    if (p.returncode != 0 or audio is None) and str(plan_now.get("vae", "")).startswith("scrag"):
        # the alternative decoder did not load -> same song with the stock decoder, remember for next runs
        ctx.note("ScragVAE failed in acestep.cpp - rendering with the stock decoder (set acestep.vae in config.yaml).")
        plan_now["vae"] = "vae-BF16.gguf"
        write_json(synth_req, plan_now)
        ctx.cfg["acestep"]["vae"] = ""
        t1 = time.time()
        try:
            p = media.run(_cmd(a["synth_cmd"], {**repl, "{request}": str(synth_req)}, extra(bins["ace-synth"])),
                          ctx.log, timeout=timeout, cwd=str(work), env=run_env, priority=prio)
        except StepError as e:
            return False, f"ace-synth {e}"
        audio = _newest_audio(work, t1)
    if p.returncode != 0 or audio is None or not file_ok(audio, 50_000):
        tail = "\n".join(((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-8:])
        return False, f"ace-synth code {p.returncode}: {tail}"
    media.ffmpeg(ctx.paths, ["-i", str(audio), "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", str(out)], ctx.log, timeout=1800)
    if synth_req != request:  # keep the song plan (audio codes) - the A/B quality test re-renders from it
        shutil.copy2(synth_req, out.with_suffix(".plan.json"))
    ctx.timings.setdefault("ace_lm_min", round((t1 - t0) / 60, 1))
    ctx.timings.setdefault("ace_synth_min", round((time.time() - t1) / 60, 1))
    shutil.rmtree(work, ignore_errors=True)
    return True, ""


def describe(cfg: dict, model: str) -> str:
    return {"turbo": "ACE-Step 1.5 turbo", "xl-turbo": "ACE-Step 1.5 XL turbo"}.get(model, "ACE-Step 1.5")

