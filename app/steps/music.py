"""Step 3: music - N variants of a full-length track (2:50-5:20).

Engine (music.engine): "acestep" = ACE-Step 1.5 via acestep.cpp (default since 2.0, whole track in one generation,
see app/engines/acestep.py) or "sa3" = Stable Audio 3 via sa3.cpp (described below).

Stable Audio 3 Small generates at most ~2 minutes at once, so long tracks are ARRANGED:
the brief splits the length into sections (intro / main / variation / climax / outro ...).
Every section uses the same style core (style, key, tempo, instruments) and the same seed,
then the sections are joined with long equal-power crossfades into one track per variant.
With a model that can do the whole length at once (e.g. Stable Audio 3 Medium) a single
generation is used instead (music.mode / native_max_seconds in config).
"""
from __future__ import annotations

import random
import time
from pathlib import Path

from ..common import media
from ..common.util import Ctx, StepError, engine_speed, file_ok, learn_engine_cap, learn_engine_speed, write_json


def _help(ctx: Ctx, exe: Path) -> str:
    cache = ctx.paths.state / "sa3_help.txt"
    if not file_ok(cache):
        # same program prefix as the generate command (works when the command starts with an interpreter)
        cmd = ctx.cfg["music"]["command"]
        prefix = [c.replace("{exe}", str(exe)) for c in cmd[: cmd.index("{exe}") + 1]] if "{exe}" in cmd else [str(exe)]
        p = media.run([*prefix, "--help"], ctx.log, timeout=60, cwd=str(ctx.paths.sa3_dir or exe.parent))
        cache.write_text((p.stdout or "") + (p.stderr or ""), encoding="utf-8")
    return cache.read_text(encoding="utf-8", errors="replace")


def _generate(ctx: Ctx, exe: Path, prompt: str, seconds: int, seed: int, out: Path, flags: dict) -> tuple[bool, str]:
    mcfg = ctx.cfg["music"]
    repl = {"{exe}": str(exe), "{model}": str(mcfg["model"]), "{prompt}": prompt,
            "{duration}": str(int(seconds)), "{out}": str(out), "{seed}": str(seed)}
    cmd = []
    for part in mcfg["command"]:
        for k, v in repl.items():
            part = part.replace(k, v)
        cmd.append(part)
    if flags["seed"] and "--seed" not in cmd:
        cmd += ["--seed", str(seed)]
    if flags["encoding"]:
        cmd += ["--encoding", flags["encoding"]]
    if flags.get("steps"):
        cmd += ["--steps", str(flags["steps"])]
    cmd += [str(a) for a in (mcfg.get("extra_args") or [])]
    # Timeout: config value, but never less than 3x the time measured on this CPU (slow CPU / medium model).
    timeout = int(mcfg["timeout_minutes"]) * 60
    speed = engine_speed(ctx.paths, str(mcfg["model"]))
    if not speed and str(mcfg["model"]) == "medium":
        # first medium run: not measured yet -> assume up to 4x slower than small, at least 4 h
        small = engine_speed(ctx.paths, "small-music")
        timeout = max(timeout, 4 * 3600)
        speed = small * 4 if small else None
    if speed:
        timeout = max(timeout, int(speed * seconds * 3) + 600)
    p = media.run(cmd, ctx.log, timeout=timeout, cwd=str(ctx.paths.sa3_dir or exe.parent))
    if p.returncode != 0 or not file_ok(out, 50_000):
        tail = "\n".join(((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-6:])
        return False, f"code {p.returncode}: {tail}"
    return True, ""


def _assemble(ctx: Ctx, parts: list[Path], out: Path, crossfade: float) -> None:
    """Join sections with equal-power crossfades into one 48 kHz stereo WAV."""
    if len(parts) == 1:
        media.ffmpeg(ctx.paths, ["-i", str(parts[0]), "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", str(out)], ctx.log, timeout=1800)
        return
    durs = [media.duration(ctx.paths, p, ctx.log) for p in parts]
    xf = max(1.0, min(crossfade, min(durs) / 3))
    args: list[str] = []
    for p in parts:
        args += ["-i", str(p)]
    chain = [f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[s{i}]" for i in range(len(parts))]
    prev = "s0"
    for i in range(1, len(parts)):
        chain.append(f"[{prev}][s{i}]acrossfade=d={xf:.2f}:c1=qsin:c2=qsin[x{i}]")
        prev = f"x{i}"
    fc = ";".join(chain)
    media.ffmpeg(ctx.paths, [*args, "-filter_complex", fc, "-map", f"[{prev}]", "-ar", "48000", "-c:a", "pcm_f32le", str(out)], ctx.log, timeout=1800)


def _variant(brief: dict, sections: list[dict], i: int) -> tuple[dict, list[dict]]:
    """2.7: variant i may be its own piece (brief['variants'] from app/variety.py): same style and sections,
    but its own key / tempo / mood / instruments. Returns (brief for the engine, sections with its prompt)."""
    specs = brief.get("variants") or []
    if len(specs) < 2:
        return brief, sections
    s, base = specs[(i - 1) % len(specs)], specs[0]
    if s.get("prompt_core") == base.get("prompt_core") and all(brief.get(k) == v for k, v in s.items()):
        return brief, sections
    vb = {**brief, **s}  # also for variant 1: brief.json may already describe the other variant (resumed run)
    return vb, [{**x, "prompt": str(x["prompt"]).replace(base["prompt_core"], s["prompt_core"])} for x in sections]


def run(ctx: Ctx, brief: dict, round_no: int = 0) -> list[Path]:
    mcfg = ctx.cfg["music"]
    vdir = ctx.out_dir / "variants"
    vdir.mkdir(parents=True, exist_ok=True)
    enc = str(mcfg.get("encoding") or "").strip()
    if str(mcfg.get("engine", "acestep")).lower() == "acestep":
        from ..engines import acestep
        miss = acestep.missing_files(ctx.cfg, ctx.paths, str(mcfg["model"]))
        if miss:
            raise StepError("ACE-Step is not complete - missing: " + ", ".join(miss)
                            + ". Run Uruchom-Setup.cmd (option 4) or see DO-POBRANIA.txt.")
        flags = {"seed": True}

        def gen(prompt, seconds, seed, part, vb=None):
            return acestep.generate(ctx, prompt, seconds, seed, part, vb or brief)
    else:
        exe = ctx.paths.sa3_exe
        if not exe or not Path(exe).exists():
            raise StepError("sa3-generate.exe not found. Run setup again (step 8, sa3.cpp) or set AIMUSIC_SA3_EXE.")
        exe = Path(exe)
        help_text = _help(ctx, exe)
        steps = int(mcfg.get("steps") or 0)
        flags = {"seed": "--seed" in help_text,
                 "steps": steps if steps > 0 and "--steps" in help_text and "--steps" not in mcfg["command"] else 0,
                 "encoding": enc if enc and "--encoding" not in mcfg["command"] and ("--encoding" in help_text or not help_text.strip()) else ""}

        def gen(prompt, seconds, seed, part, vb=None):
            return _generate(ctx, exe, prompt, seconds, seed, part, flags)
    sections = brief.get("sections") or [{"role": "full", "seconds": brief.get("target_seconds", 180), "prompt": brief["music_prompt"]}]
    replanned = False
    fell_back = False
    rng = random.Random(ctx.date.toordinal() * 100 + round_no)
    n = int(mcfg["variants"])
    total_secs = sum(s["seconds"] for s in sections)
    ctx.log.info("Music: %d variant(s) x %d section(s) (%s), %d s of audio per variant",
                 n, len(sections), " + ".join(f"{s['role']} {s['seconds']}s" for s in sections), total_secs)
    files: list[Path] = []
    i = 0
    while i < n:
        i += 1
        final = vdir / f"r{round_no}_v{i}.wav"
        if file_ok(final, 100_000):
            files.append(final)
            continue
        seed = rng.randint(1, 2**31 - 1)
        parts: list[Path] = []
        failed = None
        vb, vsections = _variant(brief, sections, i)
        if vb is not brief and i > 1:
            ctx.log.info("  variant %d = own piece: %s, %s BPM, %s", i, vb["key"], vb["bpm"], vb["mood"])
        for k, sec in enumerate(vsections, 1):
            part = vdir / f"r{round_no}_v{i}_s{k}_{sec['role']}.wav"
            if not file_ok(part, 50_000):
                ctx.log.info("  variant %d/%d, section %d/%d (%s, %d s, seed %s)...", i, n, k, len(sections),
                             sec["role"], sec["seconds"], seed if flags["seed"] else "-")
                t0 = time.time()
                if ctx.progress:
                    from ..common.progress import music_speed
                    span = 1 / n / len(sections)
                    base = ((i - 1) + (k - 1) / len(sections)) / n
                    what = f"wersja {i}/{n}" + (f", część {k}/{len(sections)}" if len(sections) > 1 else "")
                    with ctx.progress.timed(base, span, music_speed(ctx.paths, str(mcfg["model"]))[0] * sec["seconds"], what):
                        ok, err = gen(sec["prompt"], sec["seconds"], seed, part, vb)
                else:
                    ok, err = gen(sec["prompt"], sec["seconds"], seed, part, vb)
                took = (time.time() - t0) / 60
                ctx.timings[f"music_v{i}_s{k}_min"] = round(took, 1)
                if not ok:
                    failed = f"Variant {i} section {k} ({sec['role']}) failed ({err})"
                    if str(mcfg["model"]) == "medium" and not files and not fell_back:
                        # Medium could not run (memory, missing files...) -> continue with the small model
                        from ..common.config import has_model_files
                        if has_model_files(ctx.paths, "small-music", enc or "f32"):
                            from .brief import plan_sections
                            from ..common.util import engine_cap
                            ctx.note(f"Medium model failed ({err[:200]}) - falling back to small-music with sections.")
                            mcfg["model"] = "small-music"
                            sections = plan_sections(brief, ctx.cfg, rng, engine_cap(ctx.paths, "small-music"))
                            brief["sections"] = sections
                            write_json(ctx.out_dir / "brief.json", brief)
                            fell_back = True
                            for f in vdir.glob(f"r{round_no}_v{i}_s*.wav"):
                                f.unlink(missing_ok=True)
                            failed = "replan"
                    elif str(mcfg["model"]) == "xl-turbo" and not files and not fell_back:
                        from ..common.config import has_model_files
                        if has_model_files(ctx.paths, "turbo", "", ctx.cfg):
                            ctx.note(f"ACE-Step XL failed ({err[:200]}) - falling back to the 2B turbo model.")
                            mcfg["model"] = "turbo"
                            fell_back = True
                            failed = "replan"
                    break
                got = media.duration(ctx.paths, part, ctx.log)
                ctx.log.info("    ready in %.1f min (%.0f s of audio)", took, got)
                if got >= 10:
                    learn_engine_speed(ctx.paths, str(mcfg["model"]), took * 60 / got)
                if got < 0.9 * sec["seconds"] and not replanned and got >= 10:
                    # The engine delivers less than requested -> remember its real limit and re-plan into sections.
                    learn_engine_cap(ctx.paths, str(mcfg["model"]), int(got))
                    from .brief import plan_sections
                    sections = plan_sections(brief, ctx.cfg, rng, int(got))
                    brief["sections"] = sections
                    write_json(ctx.out_dir / "brief.json", brief)
                    ctx.note(f"Music engine produced only {got:.0f} s per generation - switched to {len(sections)} sections.")
                    replanned = True
                    for f in vdir.glob(f"r{round_no}_v{i}_s*.wav"):
                        f.unlink(missing_ok=True)
                    failed = "replan"
                    break
            parts.append(part)
        if failed == "replan":
            i -= 1  # redo this variant with the new section plan
            continue
        if failed:
            ctx.note(failed)
            continue
        _assemble(ctx, parts, final, float(mcfg["crossfade_seconds"]))
        files.append(final)
        ctx.log.info("  variant %d assembled: %.0f s", i, media.duration(ctx.paths, final, ctx.log))
    if not files:
        raise StepError("No music variant was generated. Check the music engine messages above; "
                        "if the command line changed, adjust music.command in config.yaml.")
    return files
