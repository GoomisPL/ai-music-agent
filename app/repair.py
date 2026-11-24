"""Self-repair: diagnose the last failed run and try to fix it - locally, without any cloud AI.

Order of attempts (each one is verified by app/selftest.py; a change that breaks the selftest is rolled back):
  1. Playbook of known problems -> safe fixes in config/config.auto.yaml or in the workspace
     (stale lock, sa3 options changed, video filter too heavy, quality gate too strict, timeouts, disk space).
  2. Unknown error inside app/*.py -> the local coding model (Ollama, e.g. qwen2.5-coder) proposes ONE minimal
     search/replace patch. It is applied only if the code still compiles AND the selftest passes. Before every
     change the whole app/ folder is backed up to state/app-backups/<time>/.

Guard rails: secrets are never read or sent; selftest.py, repair.py and preflight.py are never modified by the
model; the AI-disclosure checks in the selftest make it impossible to "fix" a problem by removing the AI label.

    python app/repair.py            (or: AIMusicAgent.exe --repair)
Exit code: 0 = something was fixed (run again), 1 = nothing could be fixed automatically, 3 = needs the user.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
import yaml  # noqa: E402

from app.common import config, llm  # noqa: E402
from app.common.util import get_logger, read_json  # noqa: E402

APP = Path(__file__).resolve().parent
PROTECTED = {"selftest.py", "repair.py", "preflight.py"}
USER_ACTION = 3


class Repair:
    def __init__(self):
        self.paths = config.paths()
        self.paths.ensure()
        self.log = get_logger(self.paths.logs)
        self.cfg = config.load()
        self.rcfg = self.cfg.get("repair", {})
        self.auto_file = self.paths.home / "config" / "config.auto.yaml"
        self.report: list[str] = []

    # ------------------------------------------------------------ helpers
    def say(self, text: str) -> None:
        self.report.append(text)
        self.log.info("[repair] %s", text)

    def last_failure(self) -> tuple[dict, str]:
        summaries = sorted(self.paths.output.glob("*/run-summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        summary = next((read_json(s) for s in summaries if (read_json(s) or {}).get("exit_code", 0) != 0), None) or {}
        logs = sorted(self.paths.logs.glob("agent-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        tail = ""
        if logs:
            lines = logs[0].read_text(encoding="utf-8", errors="replace").splitlines()
            # only the LAST run - older errors of the same day must not trigger wrong fixes
            starts = [i for i, ln in enumerate(lines) if "=== AI Music Agent - run" in ln]
            if starts:
                lines = lines[starts[-1]:]
            tail = "\n".join(lines[-200:])
        return summary, tail

    def set_auto(self, dotted: str, value) -> None:
        data = {}
        if self.auto_file.exists():
            data = yaml.safe_load(self.auto_file.read_text(encoding="utf-8")) or {}
        node = data
        keys = dotted.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        self.auto_file.write_text("# Automatic corrections written by app/repair.py - delete this file to undo them\n"
                                  + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.say(f"config.auto.yaml: {dotted} = {value!r}")

    def selftest(self) -> bool:
        p = subprocess.run([sys.executable, str(APP / "selftest.py")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
        tail = "\n".join((p.stdout + "\n" + p.stderr).strip().splitlines()[-25:])
        self.say(f"selftest exit {p.returncode}:\n{tail}")
        return p.returncode == 0

    def backup(self) -> Path:
        dest = self.paths.state / "app-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copytree(APP, dest, ignore=shutil.ignore_patterns("__pycache__"))
        backups = sorted((self.paths.state / "app-backups").iterdir())
        for old in backups[:-10]:
            shutil.rmtree(old, ignore_errors=True)
        return dest

    def restore(self, src: Path) -> None:
        for f in src.rglob("*.py"):
            target = APP / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        self.say(f"code restored from backup {src.name}")

    # ------------------------------------------------------------ playbook
    def playbook(self, err: str, notes: str) -> int | None:
        text = err + "\n" + notes
        if re.search(r"Another run is in progress", text):
            lock = self.paths.state / "run.lock"
            lock.unlink(missing_ok=True)
            self.say("removed a stale run.lock")
            return 0
        if re.search(r"login expired|Not authorized yet|invalid_grant", text):
            self.say("YouTube login expired - run the desktop shortcut 'AI Music Agent - logowanie YouTube'.")
            return USER_ACTION
        if re.search(r"sa3-generate\.exe not found|Program not found|Missing .*client_secret", text):
            self.say("Missing software or credentials - see DO-POBRANIA.txt in the workspace.")
            return USER_ACTION
        # ---- ACE-Step (acestep.cpp)
        if re.search(r"ACE-Step is not complete", text):
            self.say("ACE-Step binaries or models are missing - see DO-POBRANIA.txt, run Uruchom-Setup.cmd (option 4).")
            return USER_ACTION
        if re.search(r"ace-(synth|lm) code.*(bad_alloc|out of memory|failed to allocate|Cannot allocate)", text, re.I | re.S):
            if str(self.cfg["music"].get("model")) == "xl-turbo":
                self.set_auto("music.model", "turbo")
            elif "4B" in str(self.cfg["acestep"]["lm_model"]) or "1.7B" in str(self.cfg["acestep"]["lm_model"]):
                self.set_auto("acestep.lm_model", "acestep-5Hz-lm-0.6B-Q8_0")
            else:
                self.set_auto("music.variants", 1)
            return 0
        if re.search(r"not found in registry", text):
            # model name does not match a file in the models folder - not something to switch off
            self.say("ACE-Step model name does not match the files in the models folder - check acestep.lm_model / "
                     "music.model in config.yaml against the .gguf files (Uruchom-Setup.cmd option 4 downloads them).")
            return USER_ACTION
        if re.search(r"ace-lm code", text) and self.cfg["acestep"].get("use_lm", True):
            self.set_auto("acestep.use_lm", False)  # render from the caption only (DiT), no LM song plan
            return 0
        if re.search(r"ace-(synth|lm) code.*(unknown|unrecognized|invalid) (option|argument)", text, re.I | re.S):
            for f in self.paths.state.glob("ace-*_help.txt"):
                f.unlink(missing_ok=True)
            self.set_auto("acestep.threads", -1)
            return 0
        if re.search(r"Timeout after \d+s: ace-", text):
            self.set_auto("music.timeout_minutes", int(self.cfg["music"]["timeout_minutes"]) + 120)
            self.set_auto("music.variants", max(1, int(self.cfg["music"]["variants"]) - 1))
            return 0
        m = re.search(r"no stable-audio-3-[\w-]*?-\*?-?(F16|F32)\.gguf", text, re.I)
        if m:
            models = self.paths.sa3_dir / "models" if self.paths.sa3_dir else None
            have = {e for e in ("F16", "F32") if models and any(models.rglob(f"*dit*{e}.gguf"))}
            other = ({"F16", "F32"} - {m.group(1).upper()}) & have
            if other:
                self.set_auto("music.encoding", other.pop().lower())
                (self.paths.state / "sa3_help.txt").unlink(missing_ok=True)
                return 0
            self.say("Music model files are missing - run Uruchom-Setup.cmd (option 4) to download them.")
            return USER_ACTION
        if re.search(r"CUDA error|unsupported toolchain|llama-server process has terminated", text, re.I):
            self.set_auto("llm.force_cpu", True)
            return 0
        if re.search(r"Variant \d+ failed.*(unrecognized|unknown|invalid|usage)", text, re.I | re.S):
            return self.fix_sa3_command()
        if re.search(r"ffmpeg failed", text) and re.search(r"zoompan|Cannot allocate|out of memory", text, re.I):
            self.set_auto("video.zoom", False)
            return 0
        if re.search(r"ffmpeg failed", text) and self.cfg["video"]["width"] > 1280:
            self.set_auto("video.width", 1280)
            self.set_auto("video.height", 720)
            return 0
        if re.search(r"No variant passed the quality gate", text):
            q = self.cfg["quality"]
            self.set_auto("quality.max_silence_ratio", round(min(0.35, float(q["max_silence_ratio"]) + 0.1), 2))
            self.set_auto("quality.max_clipping_ratio", round(min(0.02, float(q["max_clipping_ratio"]) * 3), 4))
            self.set_auto("quality.min_duration_ratio", round(max(0.4, float(q["min_duration_ratio"]) - 0.1), 2))
            return 0
        if re.search(r"Timeout after \d+s: sa3", text):
            self.set_auto("music.timeout_minutes", int(self.cfg["music"]["timeout_minutes"]) + 60)
            self.set_auto("music.variants", max(1, int(self.cfg["music"]["variants"]) - 1))
            return 0
        if re.search(r"LLM attempt 3/3 failed.*(timed out|Read timed out)", text, re.S):
            self.set_auto("llm.timeout_seconds", int(self.cfg["llm"]["timeout_seconds"]) + 600)
            return 0
        if re.search(r"GB free - not enough|Low disk space", text):
            freed = 0
            for d in self.paths.archive.glob("*"):
                for f in list(d.glob("*.wav")) + list(d.glob("short.mp4")):
                    freed += f.stat().st_size
                    f.unlink(missing_ok=True)
            self.say(f"freed {freed / 1e9:.1f} GB in archive (kept final videos)")
            return 0 if freed else USER_ACTION
        return None

    def fix_sa3_command(self) -> int:
        exe = self.paths.sa3_exe
        if not exe:
            return USER_ACTION
        p = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=60)
        help_text = (p.stdout or "") + (p.stderr or "")
        opts = set(re.findall(r"(--[a-z][a-z0-9\-]*)", help_text))
        pick = lambda *names: next((n for n in names if n in opts), None)  # noqa: E731
        prompt, out = pick("--prompt", "--text", "-p"), pick("--out", "--output", "-o")
        dur, model = pick("--duration", "--seconds", "--length"), pick("--model", "--variant")
        if not (prompt and out):
            self.say(f"sa3 --help did not show prompt/output options:\n{help_text[:800]}")
            return USER_ACTION
        cmd = ["{exe}"]
        if model:
            cmd += [model, "{model}"]
        cmd += [prompt, "{prompt}"]
        if dur:
            cmd += [dur, "{duration}"]
        cmd += [out, "{out}"]
        self.set_auto("music.command", cmd)
        (self.paths.state / "sa3_help.txt").unlink(missing_ok=True)
        return 0

    # ------------------------------------------------------------ LLM patch
    def llm_patch(self, err: str, tail: str) -> int:
        if not self.rcfg.get("llm_patches", True):
            return 1
        frames = re.findall(r'File "([^"]+[\\/]app[\\/][^"]+\.py)", line (\d+)', tail)
        frames = [(Path(f), int(n)) for f, n in frames if Path(f).name not in PROTECTED and Path(f).exists()]
        if not frames:
            self.say("no traceback inside app/ code - nothing for the code model to fix")
            return 1
        file, line = frames[-1]
        rel = file.resolve().relative_to(APP.parent).as_posix()
        src = file.read_text(encoding="utf-8")
        lines = src.splitlines()
        lo, hi = max(0, line - 60), min(len(lines), line + 40)
        snippet = "\n".join(f"{i + 1:4d}| {lines[i]}" for i in range(lo, hi))
        model = self.rcfg.get("model") or self.cfg["llm"]["model"]
        host = self.cfg["llm"]["host"].rstrip("/")
        llm.ensure_server(self.cfg, self.paths, self.log)
        tb = "\n".join(tail.splitlines()[-40:])
        for attempt in range(1, int(self.rcfg.get("max_attempts", 2)) + 1):
            prompt = (
                "You fix a bug in a Python 3.11 program (AI music YouTube uploader on Windows).\n"
                "Return ONE JSON object: {\"search\": exact lines copied from the code below (without the line-number "
                "prefix), \"replace\": the corrected lines, \"explanation\": one sentence}.\n"
                "Rules: minimal change; keep behaviour otherwise identical; never remove AI disclosure, "
                "containsSyntheticMedia or the ' | AI Music' title suffix; no new dependencies; no network calls added.\n\n"
                f"ERROR:\n{err}\n\nTRACEBACK / LOG TAIL:\n{tb}\n\nFILE {rel} (lines {lo + 1}-{hi}):\n{snippet}\n")
            try:
                r = requests.post(f"{host}/api/generate", json={"model": model, "prompt": prompt, "format": "json",
                                                                "stream": False, "options": {"num_ctx": 8192, "temperature": 0.2, **llm.cpu_opts(self.cfg)}},
                                  timeout=int(self.rcfg.get("timeout_seconds", 1800)))
                patch = json.loads(r.json().get("response", "{}"))
            except Exception as e:  # noqa: BLE001
                self.say(f"code model unavailable ({model}): {e}")
                return 1
            search, replace = str(patch.get("search", "")), str(patch.get("replace", ""))
            if not search.strip() or src.count(search) != 1 or search == replace:
                self.say(f"attempt {attempt}: patch rejected (search text not found exactly once)")
                continue
            backup = self.backup()
            file.write_text(src.replace(search, replace), encoding="utf-8")
            self.say(f"attempt {attempt}: patched {rel} - {patch.get('explanation', '')}\n--- old\n{search}\n+++ new\n{replace}")
            if self.selftest():
                self.say("patch kept (selftest passed)")
                return 0
            self.restore(backup)
        return 1

    # ------------------------------------------------------------ main
    def run(self) -> int:
        summary, tail = self.last_failure()
        err = summary.get("error", "")
        notes = "\n".join(summary.get("notes", []))
        if not err and "ERROR" not in tail:
            self.say("no failed run found - nothing to repair")
            return 1
        self.say(f"last error: {err or '(see log)'}")
        rc = self.playbook(err, notes + "\n" + tail)
        if rc == 0 and not self.selftest():
            self.say("selftest failed after the config fix - reverting config.auto.yaml change is left to you (file is listed above)")
            rc = 1
        if rc is None:
            rc = self.llm_patch(err, tail)
        self.write_report(rc)
        return rc

    def write_report(self, rc: int) -> None:
        f = self.paths.logs / f"repair-{datetime.now():%Y%m%d-%H%M%S}.md"
        status = {0: "FIXED - run again", 1: "NOT FIXED automatically", USER_ACTION: "NEEDS YOUR ACTION"}.get(rc, str(rc))
        f.write_text(f"# Repair report - {status}\n\n" + "\n\n".join(self.report) + "\n", encoding="utf-8")
        print(f"\n  Repair: {status}. Report: {f}")


if __name__ == "__main__":
    sys.exit(Repair().run())
