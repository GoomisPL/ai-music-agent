"""Step 4: automatic quality gate - pick the best variant, reject broken or repetitive ones, master it."""
from __future__ import annotations

from pathlib import Path

from ..common import media
from ..common.util import Ctx, StepError, file_ok, load_history, read_json, write_json


def score(m: dict, novelty: float) -> float:
    variety = min(1.0, m["variety"] / 0.35)
    dynamics = min(1.0, m["dynamics_db"] / 12.0)
    nov = min(1.0, novelty / 0.15)
    return round(0.4 * variety + 0.3 * dynamics + 0.3 * nov, 3)


def pick_bonus(ctx: Ctx, results: dict, best_name: str) -> dict | None:
    """Runner-up that is good enough for its own video: passed the gate, score >= bonus.min_score,
    not far below the winner and clearly different from it (fingerprint distance)."""
    bc = ctx.cfg.get("bonus", {})
    if not bc.get("enabled"):
        return None
    if len(results) < 2:
        if int(ctx.cfg["music"].get("variants", 2)) >= 2:
            ctx.log.info("Second track: did not pass the quality gate - only one video today")
        return None
    qc = ctx.cfg["quality"]
    best = results[best_name]
    others = sorted((k for k in results if k != best_name), key=lambda k: -results[k]["score"])
    for k in others:
        r = results[k]
        dist = media.cosine_distance(r["_m"]["fingerprint"], best["_m"]["fingerprint"])
        why = []
        if r["score"] < float(bc.get("min_score", 0.7)):
            why.append(f"score {r['score']:.2f} < {float(bc.get('min_score', 0.7)):.2f}")
        if best["score"] - r["score"] > float(bc.get("max_score_gap", 0.15)):
            why.append(f"{best['score'] - r['score']:.2f} below the first track")
        if dist < float(bc.get("min_distance") or qc["min_distance_to_history"]):
            why.append(f"too similar to the first track (distance {dist:.3f})")
        ctx.log.info("Second track %s: score %.2f, distance to the first %.3f -> %s", k, r["score"], dist,
                     "GOOD - own video + Shorts tomorrow" if not why else "not published (" + "; ".join(why) + ")")
        if not why:
            return {"name": k, "score": r["score"], "duration": r["_m"]["duration"], "distance": round(dist, 4),
                    "fingerprint": r["_m"]["fingerprint"], "energy_per_sec": r["_m"]["energy_per_sec"]}
    return None


def run(ctx: Ctx, variants: list[Path], target: float | None = None) -> dict:
    out = ctx.out_dir / "quality.json"
    track = ctx.out_dir / "track.wav"
    if file_ok(out) and file_ok(track, 100_000):
        q = read_json(out)
        if q.get("ok"):
            return q
    qc = ctx.cfg["quality"]
    target = float(target or ctx.cfg["music"]["min_seconds"])
    max_len = float(ctx.cfg["music"]["max_seconds"])
    hist = [h for h in load_history(ctx.paths)[-int(qc["history_window"]):] if h.get("fingerprint")]
    results, rejected = {}, {}
    for v in variants:
        x, sr = media.load_mono(ctx.paths, v, ctx.log)
        m = media.analyze(x, sr)
        novelty = min((media.cosine_distance(m["fingerprint"], h["fingerprint"]) for h in hist), default=1.0)
        reason = None
        if m["duration"] < target * float(qc["min_duration_ratio"]):
            reason = f"too short ({m['duration']:.0f}s)"
        elif m["silence_ratio"] > float(qc["max_silence_ratio"]):
            reason = f"silence {m['silence_ratio']:.0%}"
        elif m["clipping_ratio"] > float(qc["max_clipping_ratio"]):
            reason = f"clipping {m['clipping_ratio']:.2%}"
        elif novelty < float(qc["min_distance_to_history"]):
            reason = f"too similar to a previous track (distance {novelty:.3f})"
        s = score(m, novelty)
        ctx.log.info("  %s: score %.2f, silence %.0f%%, variety %.2f, dynamics %.1f dB, novelty %.3f%s",
                     v.name, s, m["silence_ratio"] * 100, m["variety"], m["dynamics_db"], novelty,
                     f" -> REJECTED: {reason}" if reason else "")
        entry = {k: m[k] for k in ("duration", "silence_ratio", "clipping_ratio", "dynamics_db", "variety")}
        entry.update(score=s, novelty=round(novelty, 4))
        if reason:
            rejected[v.name] = {**entry, "reason": reason}
        else:
            results[v.name] = {**entry, "_m": m}
    if not results:
        # Soft novelty rule: if every variant failed ONLY because it resembles older tracks,
        # keep the best of them (with a note) instead of throwing away hours of CPU work.
        similar = {k: v for k, v in rejected.items() if v["reason"].startswith("too similar")}
        if similar:
            name = max(similar, key=lambda k: similar[k]["score"])
            ctx.note(f"All variants resemble older tracks - keeping the best one ({name}).")
            v = next(x for x in variants if x.name == name)
            x, sr = media.load_mono(ctx.paths, v, ctx.log)
            entry = rejected.pop(name)
            entry.pop("reason", None)
            results[name] = {**entry, "_m": media.analyze(x, sr)}
    if not results:
        q = {"ok": False, "rejected": rejected}
        write_json(out, q)
        return q

    best_name = max(results, key=lambda k: results[k]["score"])
    best = next(v for v in variants if v.name == best_name)
    bonus = pick_bonus(ctx, results, best_name)
    if bonus:
        bonus["_file"] = next(v for v in variants if v.name == bonus["name"])
    m = results[best_name].pop("_m")
    for r in results.values():
        r.pop("_m", None)
    dur = m["duration"]
    lufs = ctx.cfg["music"]["target_lufs"]
    dur = min(dur, max_len)  # never longer than the allowed maximum (a native long model may overshoot)
    raw_spec = {}
    try:
        x, sr = media.load_mono(ctx.paths, best, ctx.log, sr=44100)
        raw_spec = media.spectrum_report(x, sr)
        ctx.log.info("Raw engine audio: highs up to ~%s Hz, noise floor %s dBFS",
                     raw_spec.get("bandwidth_hz"), raw_spec.get("noise_floor_dbfs"))
    except Exception as e:  # noqa: BLE001 - diagnostics only
        ctx.log.debug("spectrum report failed: %s", e)
    mastering = media.master(ctx.paths, best, track, dur, ctx.cfg, ctx.log, raw_spec.get("noise_floor_dbfs"))
    if not file_ok(track, 100_000):
        raise StepError("Mastering produced no track.wav")
    q = {"ok": True, "chosen": best_name, "scores": results, "rejected": rejected, "duration": dur,
         "fingerprint": m["fingerprint"], "energy_per_sec": m["energy_per_sec"], "target_lufs": lufs,
         "raw_audio": raw_spec, "mastering": mastering}
    if bonus:  # second good variant -> its own video later (app/bonus.py)
        try:
            bdir = ctx.out_dir / "bonus"
            bdir.mkdir(exist_ok=True)
            bdur = min(bonus["duration"], max_len)
            media.master(ctx.paths, bonus.pop("_file"), bdir / "track.wav", bdur, ctx.cfg, ctx.log, None)
            q["bonus"] = {**bonus, "duration": bdur}
            ctx.log.info("Second good variant kept for a later video: %s (score %.2f)", bonus["name"], bonus["score"])
        except StepError as e:
            ctx.note(f"Second variant not kept: {e}")
    write_json(out, q)
    ctx.log.info("Quality: chose %s (score %.2f), %d rejected", best_name, results[best_name]["score"], len(rejected))
    return q
