"""ffmpeg helpers and lightweight audio analysis (numpy only - no librosa needed)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from .util import StepError

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

if sys.platform == "win32":
    # No Windows error message boxes from child programs (missing/broken DLL, crash dialogs): the agent runs
    # unattended, an open dialog would block it for hours. Errors still arrive as exit codes and log text.
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)  # FAILCRITICALERRORS|NOGPFAULTERRORBOX|NOOPENFILEERRORBOX
    except Exception:  # noqa: BLE001
        pass


PRIORITY = {"normal": 0, "above_normal": 0x00008000, "high": 0x00000080, "below_normal": 0x00004000}


def run(cmd: list, log, timeout: int = 3600, cwd: str | None = None, env: dict | None = None,
        priority: str = "normal") -> subprocess.CompletedProcess:
    log.debug("RUN %s", " ".join(str(c) for c in cmd))
    flags = NO_WINDOW | (PRIORITY.get(priority, 0) if sys.platform == "win32" else 0)
    try:
        p = subprocess.run(
            [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags,
            env=({**os.environ, **env} if env else None),
        )
    except FileNotFoundError as e:
        raise StepError(f"Program not found: {cmd[0]} ({e})") from e
    except subprocess.TimeoutExpired as e:
        raise StepError(f"Timeout after {timeout}s: {Path(str(cmd[0])).name}") from e
    return p


def ffmpeg(paths, args: list, log, timeout: int = 7200) -> None:
    p = run([paths.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], log, timeout=timeout)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-8:])
        raise StepError(f"ffmpeg failed (code {p.returncode}):\n{tail}")


def duration(paths, file: Path, log) -> float:
    p = run([paths.ffmpeg, "-hide_banner", "-i", str(file)], log, timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr or "")
    if not m:
        raise StepError(f"Cannot read duration of {file.name}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def load_mono(paths, file: Path, log, sr: int = 22050) -> tuple[np.ndarray, int]:
    """Decode any audio to mono float32 via ffmpeg (32-bit float, so engine overs above 0 dBFS are kept -
    they are not clipping, the mastering gain/limiter brings them down)."""
    tmp = file.with_suffix(".analysis.raw")
    ffmpeg(paths, ["-i", str(file), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-c:a", "pcm_f32le", str(tmp)], log, timeout=600)
    try:
        x = np.fromfile(str(tmp), dtype="<f4").astype(np.float32)
    finally:
        tmp.unlink(missing_ok=True)
    return np.nan_to_num(x), sr


def clipping(x: np.ndarray) -> float:
    """Share of samples in hard-clipped flat tops: runs of >= 3 equal samples at the peak level.
    Float overs (smooth peaks above 1.0) do not count - they are fixed losslessly by gain."""
    if x.size == 0:
        return 0.0
    peak = float(np.max(np.abs(x)))
    if peak < 0.5:
        return 0.0
    hot = np.abs(x) >= peak * 0.995
    flat = np.concatenate([[False], np.abs(np.diff(x)) < 1e-6])
    run = hot & flat & np.concatenate([[False], hot[:-1]])
    # a sample counts when it and its two predecessors sit flat at the peak
    run3 = run & np.concatenate([[False], run[:-1]])
    return float(np.mean(run3))


def analyze(x: np.ndarray, sr: int) -> dict:
    """Simple, robust descriptors used by the quality gate."""
    if x.size < sr:
        return {"duration": x.size / sr, "silence_ratio": 1.0, "clipping_ratio": 0.0,
                "dynamics_db": 0.0, "variety": 0.0, "fingerprint": [0.0] * 24, "energy_per_sec": []}
    hop = int(sr * 0.05)
    n = x.size // hop
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    silence_ratio = float(np.mean(db < -45.0))
    clipping_ratio = clipping(x)
    x = x / max(1.0, float(np.max(np.abs(x))))  # float overs -> analyse at the level mastering will use
    active = db[db > -45.0]
    dynamics = float(np.percentile(active, 90) - np.percentile(active, 10)) if active.size > 10 else 0.0

    # Spectral features on 2048-sample windows
    win = 2048
    step = sr // 2
    starts = range(0, x.size - win, step)
    hann = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(40, sr / 2, 25)
    bands = []
    centroids = []
    for s in starts:
        spec = np.abs(np.fft.rfft(x[s:s + win] * hann)) + 1e-9
        centroids.append(float(np.sum(freqs * spec) / np.sum(spec)))
        e = [np.log(np.mean(spec[(freqs >= lo) & (freqs < hi)] ** 2) + 1e-12) for lo, hi in zip(edges[:-1], edges[1:])]
        bands.append(e)
    centroids_a = np.array(centroids)
    variety = float(np.std(centroids_a) / (np.mean(centroids_a) + 1e-9)) if centroids_a.size else 0.0
    fp = np.mean(np.array(bands), axis=0) if bands else np.zeros(24)
    fp = (fp - fp.mean()) / (fp.std() + 1e-9)

    sec = sr
    m = x.size // sec
    energy = np.sqrt(np.mean(x[: m * sec].reshape(m, sec) ** 2, axis=1)).round(5).tolist() if m else []
    return {
        "duration": round(x.size / sr, 2),
        "silence_ratio": round(silence_ratio, 4),
        "clipping_ratio": round(clipping_ratio, 5),
        "dynamics_db": round(dynamics, 2),
        "variety": round(variety, 4),
        "fingerprint": [round(float(v), 4) for v in fp],
        "energy_per_sec": energy,
    }


def cosine_distance(a: list, b: list) -> float:
    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    if va.shape != vb.shape or not va.any() or not vb.any():
        return 1.0
    return float(1 - np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-12))


def best_window(energy: list, seconds: int, skip_start: int = 8) -> int:
    """Start second of the loudest window - used to cut the Short."""
    if len(energy) <= seconds + skip_start:
        return 0
    e = np.array(energy, dtype=float)
    sums = np.convolve(e, np.ones(seconds), mode="valid")
    sums[:skip_start] = -1
    return int(np.argmax(sums))


def windows(energy: list, seconds: int, n: int, skip_start: int = 8) -> list[int]:
    """Start seconds of up to n loud, non-overlapping windows (best first) - one per Short."""
    if len(energy) <= seconds + skip_start:
        return [0]
    e = np.array(energy, dtype=float)
    sums = np.convolve(e, np.ones(seconds), mode="valid")
    sums[:skip_start] = -1
    if len(e) >= seconds * n + skip_start + 8:
        sums[max(0, len(e) - seconds - 4):] = -1  # avoid the fade-out at the very end (when there is room)
    out: list[int] = []
    for _ in range(max(1, n)):
        i = int(np.argmax(sums))
        if sums[i] < 0:
            break
        out.append(i)
        sums[max(0, i - seconds + 1):i + seconds] = -1
    if len(out) < n:  # short track: a greedy pick in the middle can block both sides -> tile from the start
        tile = list(range(skip_start, len(e) - seconds + 1, seconds))[:n]
        if len(tile) > len(out):
            out = tile
    return out or [best_window(energy, seconds, skip_start)]


DENOISE_NR = {"off": 0, "light": 5, "strong": 10}  # afftdn noise reduction in dB


def loudness(paths, src: Path, pre: str, log, graph: str | None = None) -> dict:
    """EBU R128 measurement (integrated LUFS, true peak, LRA) via loudnorm print_format=json.
    `graph`: a filter_complex that starts at [0:a] and ends with the label [x] (used instead of `pre`)."""
    if graph:
        args = ["-filter_complex", graph + ";[x]loudnorm=print_format=json[o]", "-map", "[o]"]
    else:
        args = ["-af", (pre + "," if pre else "") + "loudnorm=print_format=json"]
    p = run([paths.ffmpeg, "-hide_banner", "-nostats", "-i", str(src), *args, "-f", "null", "-"], log, timeout=3600)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr or "", re.S)
    if p.returncode != 0 or not m:
        raise StepError("Cannot measure loudness:\n" + "\n".join((p.stderr or "").strip().splitlines()[-6:]))
    d = json.loads(m.group(0))
    return {"lufs": float(d["input_i"]), "true_peak": float(d["input_tp"]), "lra": float(d["input_lra"]),
            "threshold": float(d["input_thresh"])}


STEREO_BANDS = ((120, 1000), (1000, 4000), (4000, 11000))  # side gain is set per band


def stereo_graph(chain: list[str], gains: list[float] | None, mono_bass_hz: int = 120) -> str:
    """filter_complex: [0:a] -> chain -> (optional) mid/side with per-band side gains -> [x]"""
    pre = ",".join(chain) if chain else "anull"
    if not gains:
        return f"[0:a]{pre}[x]"
    x1, x2 = STEREO_BANDS[0][1], STEREO_BANDS[1][1]
    g1, g2, g3 = gains
    return (f"[0:a]{pre},asplit=2[pa][pb];"
            "[pa]pan=mono|c0=0.5*c0+0.5*c1[mid];"
            f"[pb]pan=mono|c0=0.5*c0-0.5*c1,highpass=f={mono_bass_hz}:poles=2,"
            f"acrossover=split={x1} {x2}:order=4th[s1][s2][s3];"
            f"[s1]volume={g1:.3f}[t1];[s2]volume={g2:.3f}[t2];[s3]volume={g3:.3f}[t3];"
            "[t1][t2][t3]amix=inputs=3:normalize=0[side];"
            "[mid][side]join=inputs=2:channel_layout=stereo[ms];"
            "[ms]pan=stereo|c0=c0+c1|c1=c0-c1[x]")


def master(paths, src: Path, dst: Path, seconds: float, cfg: dict, log, noise_floor: float | None = None) -> dict:
    """Clean, predictable mastering instead of single-pass loudnorm (which works like an automatic volume
    control: it lifts quiet passages - and the background hiss with them - and makes the mix 'breathe').

    1. highpass (rumble) + optional denoise (hiss) + fades,
    2. measure loudness, 3. ONE constant gain to the target LUFS + true-peak limiter, 24-bit 48 kHz."""
    a = cfg.get("audio", {})
    lufs = float(cfg["music"]["target_lufs"])
    fade_out = max(0.0, seconds - 6)
    chain = []
    if a.get("highpass_hz"):
        chain.append(f"highpass=f={int(a['highpass_hz'])}:poles=2")
    # off by default: spectral denoising of MUSIC smears reverb tails and pads ("underwater / low-bitrate MP3")
    nr = DENOISE_NR.get(str(a.get("denoise", "off")).lower(), 0)
    if nr:
        # afftdn needs the real noise level of THIS track - measured on the raw audio (quietest frames)
        nf = -50.0 if noise_floor is None else max(-70.0, min(-30.0, float(noise_floor)))
        chain.append(f"afftdn=nr={nr}:nf={nf:.0f}")
    stereo: dict = {"side_mid_db": None, "side_gains": None}
    gains = None
    if str(a.get("stereo_fix", "auto")).lower() == "auto":
        # ACE-Step's decoder leaves the mid range almost uncorrelated between L and R (side >= mid):
        # on headphones that is the phasey, smeared "many codecs" sound; in mono the middle cancels out.
        # Per band, bring the side signal down to a normal music level (side_target_db below mid).
        r = side_mid_db(paths, src, log)
        if r:
            target = float(a.get("side_target_db", -6))
            gains = [max(0.3, min(1.0, 10 ** ((target - v) / 20))) for v in r]
            stereo = {"side_mid_db": [round(float(v), 1) for v in r], "side_gains": [round(float(g), 2) for g in gains]}
            if min(gains) > 0.97:
                gains = None
    chain += ["afade=t=in:d=1.5", f"afade=t=out:st={fade_out:.2f}:d=6"]
    graph = stereo_graph(chain, gains, int(a.get("mono_bass_hz", 120)))
    meas = loudness(paths, src, "", log, graph=graph)
    gain = max(-20.0, min(float(a.get("max_gain_db", 15)), lufs - meas["lufs"]))
    limit = 10 ** (float(a.get("true_peak_db", -1.5)) / 20)
    final = graph.replace("[x]", "[y]") + f";[y]volume={gain:.2f}dB,alimiter=limit={limit:.4f}:attack=10:release=200:level=disabled[o]"
    ffmpeg(paths, ["-i", str(src), "-filter_complex", final, "-map", "[o]", "-t", f"{seconds:.2f}", "-ar", "48000",
                   "-ac", "2", "-c:a", "pcm_s24le", str(dst)], log, timeout=3600)
    log.info("Mastering: %.1f LUFS -> gain %+.1f dB, denoise %s, stereo side/mid %s dB -> side gains %s, peak limit %s dBTP",
             meas["lufs"], gain, a.get("denoise", "off"), stereo["side_mid_db"], stereo["side_gains"],
             a.get("true_peak_db", -1.5))
    return {"input_lufs": meas["lufs"], "input_true_peak": meas["true_peak"], "input_lra": meas["lra"],
            "gain_db": round(gain, 2), "denoise": a.get("denoise", "off"), **stereo}


def side_mid_db(paths, src: Path, log) -> list[float] | None:
    """Side energy relative to mid (dB) for each of STEREO_BANDS. Normal music: about -6..-15 dB."""
    try:
        p = subprocess.run([paths.ffmpeg, "-v", "error", "-i", str(src), "-ac", "2", "-ar", "24000", "-f", "f32le", "-"],
                           capture_output=True, timeout=600, creationflags=NO_WINDOW)
        x = np.frombuffer(p.stdout, dtype=np.float32)
        if x.size < 24000 * 2 * 5:
            return None
        x = x[: x.size // 2 * 2].reshape(-1, 2)
        m, sd = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
        n = 4096
        win = np.hanning(n).astype(np.float32)
        f = np.fft.rfftfreq(n, 1 / 24000)
        masks = [(f >= lo) & (f < hi) for lo, hi in STEREO_BANDS]
        em = np.zeros(len(masks))
        es = np.zeros(len(masks))
        for i in range(0, len(m) - n, n):
            pm = np.abs(np.fft.rfft(m[i:i + n] * win)) ** 2
            ps = np.abs(np.fft.rfft(sd[i:i + n] * win)) ** 2
            for j, k in enumerate(masks):
                em[j] += pm[k].sum()
                es[j] += ps[k].sum()
        if (em <= 0).any() or (es <= 0).any():
            return None
        return [float(v) for v in 10 * np.log10(es / em)]
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.debug("side/mid measurement failed: %s", e)
        return None


def spectrum_report(x: np.ndarray, sr: int) -> dict:
    """Two numbers that explain 'sounds like a dictaphone': bandwidth (where the highs end) and noise floor."""
    n = 4096
    if len(x) < n * 4:
        return {}
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[:: n // 2]
    win = np.hanning(n).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2
    avg = spec.mean(axis=0)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    db = 10 * np.log10(avg + 1e-20)
    above = np.nonzero(db > db.max() - 70)[0]  # highest frequency still within 70 dB of the loudest band
    rolloff = float(freqs[above[-1]]) if len(above) else 0.0
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    rms = rms[rms > 1e-4]  # ignore digital silence (it is not 'background noise')
    floor_db = float(20 * np.log10(np.percentile(rms, 5) + 1e-9)) if len(rms) else -90.0
    return {"bandwidth_hz": round(rolloff), "noise_floor_dbfs": round(floor_db, 1)}
