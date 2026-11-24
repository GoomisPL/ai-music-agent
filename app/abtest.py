"""Sound quality A/B test (Panel: "Test jakości A/B", or run.py --ab-test).

Takes the song plan of the last generated track (same notes, same seed) and renders ~60 s twice:
  A = old settings: stock decoder (vae-BF16), engine hard-clipping (peak_clip 10), denoise "light"
  B = new settings: ScragVAE decoder, no clipping, no denoise
Both are mastered by the agent; B is also encoded to AAC 384k (what goes to YouTube).
Result: output/ab-test-<date-time>/ + OPIS.txt with numbers (bandwidth, noise floor). Listen on headphones.
"""
from __future__ import annotations

import copy
import shutil
import time
from datetime import datetime
from pathlib import Path

from .common import media
from .common.util import StepError, read_json, write_json
from .engines import acestep

SECONDS = 60


def _latest_plan(paths) -> dict | None:
    plans = sorted(paths.output.glob("*/variants/*.plan.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in plans:
        d = read_json(f, None)
        if isinstance(d, dict) and d.get("audio_codes"):
            return d
    return None


def _run(cmd: list, log, cwd: Path, what: str) -> None:
    t0 = time.time()
    print(f"  ... {what}", flush=True)
    p = media.run(cmd, log, timeout=4 * 3600, cwd=str(cwd), env={"ACESTEP_THREADS": str(acestep.cpu_cores())},
                  priority="above_normal")
    if p.returncode != 0:
        tail = "\n".join(((p.stderr or "") + (p.stdout or "")).strip().splitlines()[-8:])
        raise StepError(f"{what}: code {p.returncode}\n{tail}")
    print(f"      gotowe w {(time.time() - t0) / 60:.1f} min", flush=True)


def main(cfg: dict, paths, log) -> int:
    if acestep.missing_files(cfg, paths, str(cfg["music"]["model"])):
        log.error("ACE-Step is not complete - run Uruchom-Setup.cmd (option 4) first.")
        return 1
    bins = acestep.binaries(paths)
    out = paths.output / f"ab-test-{datetime.now():%Y%m%d-%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    a = cfg["acestep"]
    models = paths.ace_models
    has_scrag = (models / "scragvae-BF16.gguf").exists()
    repl = {"{lm}": str(bins["ace-lm"]), "{synth}": str(bins["ace-synth"]), "{models}": str(models)}

    def cmd(kind: str, request: Path) -> list[str]:  # same command templates as a normal run (config acestep.*_cmd)
        return acestep._cmd(a[f"{kind}_cmd"], {**repl, "{request}": str(request)}, [])

    plan = _latest_plan(paths)
    if plan is None:  # no track yet -> make a plan with the language model of the engine
        print("Brak planu ostatniego utworu - tworze nowy (ok. 1-3 min)...", flush=True)
        req = {"caption": "calm lofi hip hop, warm rhodes piano, soft vinyl texture, mellow drums, airy pads",
               "lyrics": "[Instrumental]", "duration": SECONDS, "seed": 42, "bpm": 80, "keyscale": "A minor",
               "timesignature": "4", "synth_model": acestep.gguf(acestep.ace_file(cfg, str(cfg["music"]["model"]))),
               "lm_model": acestep.gguf(a["lm_model"])}
        write_json(out / "request.json", req)
        _run(cmd("lm", out / "request.json"), log, out,
             "plan utworu (ace-lm)")
        plan = read_json(out / "request0.json", {})
    else:
        print(f"Plan ostatniego utworu: {plan.get('caption', '')[:90]}...", flush=True)

    base = copy.deepcopy(plan)
    codes = [c for c in str(base.get("audio_codes", "")).split(",") if c.strip()]
    sec = min(SECONDS, float(base.get("duration") or SECONDS))
    if codes:
        base["audio_codes"] = ",".join(codes[: int(sec * 5)])  # 5 codes per second
    base.update(duration=sec, output_format="wav32", synth_batch_size=1, lm_batch_size=1)
    base["synth_model"] = acestep.gguf(base.get("synth_model") or acestep.ace_file(cfg, str(cfg["music"]["model"])))

    variants = {"A": {"vae": "vae-BF16.gguf", "peak_clip": 10}}
    if has_scrag:
        variants["B"] = {"vae": "scragvae-BF16.gguf", "peak_clip": 0}
    else:
        variants["B"] = {"vae": "vae-BF16.gguf", "peak_clip": 0}
        print("UWAGA: brak scragvae-BF16.gguf - wariant B rozni sie tylko brakiem obcinania i odszumiania. "
              "Uruchom setup (opcja 4), zeby pobrac lepszy dekoder.", flush=True)
    raw: dict[str, Path] = {}
    for name, extra in variants.items():
        d = out / f"render_{name}"
        d.mkdir(exist_ok=True)
        req = {**base, **extra}
        write_json(d / "request0.json", req)
        _run(cmd("synth", d / "request0.json"), log, d,
             f"render {name} ({extra['vae']}, peak_clip {extra['peak_clip']})")
        audio = next((f for f in d.iterdir() if f.suffix.lower() in (".wav", ".mp3")), None)
        if audio is None:
            raise StepError(f"render {name}: no audio file")
        raw[name] = out / f"{name}_surowy_{Path(extra['vae']).stem}.wav"
        media.ffmpeg(paths, ["-i", str(audio), "-c:a", "pcm_f32le", str(raw[name])], log)
        shutil.rmtree(d, ignore_errors=True)

    print("  ... mastering obu wersji", flush=True)
    lines = []
    for name, denoise, label in (("A", "light", "1_STARE_ustawienia"), ("B", "off", "2_NOWE_ustawienia")):
        c = copy.deepcopy(cfg)
        c.setdefault("audio", {})["denoise"] = denoise
        x, sr = media.load_mono(paths, raw[name], log, sr=44100)
        spec = media.spectrum_report(x, sr)
        dst = out / f"{label}.wav"
        media.master(paths, raw[name], dst, sec, c, log, spec.get("noise_floor_dbfs"))
        x2, sr2 = media.load_mono(paths, dst, log, sr=44100)
        spec2 = media.spectrum_report(x2, sr2)
        lines.append(f"{label}: dekoder {variants[name]['vae']}, obcinanie {variants[name]['peak_clip']}, odszumianie {denoise}"
                     f"\n    surowy: gory do ~{spec.get('bandwidth_hz')} Hz, szum tla {spec.get('noise_floor_dbfs')} dBFS"
                     f"\n    po masteringu: gory do ~{spec2.get('bandwidth_hz')} Hz, szum tla {spec2.get('noise_floor_dbfs')} dBFS")
    aac = out / "3_NOWE_po_kodowaniu_YouTube_AAC384.m4a"
    media.ffmpeg(paths, ["-i", str(out / "2_NOWE_ustawienia.wav"), "-c:a", "aac", "-b:a",
                         str(cfg.get("audio", {}).get("aac_bitrate", "384k")), str(aac)], log)
    text = ("TEST JAKOSCI A/B - ten sam fragment utworu (te same nuty i seed), roznia sie tylko ustawienia dzwieku.\n"
            "Sluchaj na sluchawkach, najlepiej przy cichszych, spokojnych fragmentach i w tle.\n\n"
            + "\n".join(lines)
            + "\n\n3_NOWE_po_kodowaniu_YouTube_AAC384.m4a = wersja 2 po kodowaniu jak do wysylki na YouTube.\n"
            "Pliki *_surowy_* = prosto z silnika, przed masteringiem agenta.\n"
            "Jesli 2 brzmi lepiej - nic nie rob, nowe ustawienia sa domyslne. Jesli gorzej - napisz, co slyszysz.\n")
    (out / "OPIS.txt").write_text(text, encoding="utf-8")
    print("\n" + text + f"\nPliki: {out}", flush=True)
    return 0
