"""AI Music Agent - desktop window (tkinter, part of the standard Windows Python - nothing to install).

    AIMusicAgent.exe --gui         (desktop shortcut "AI Music Agent - Panel")
    .venv\\Scripts\\pythonw.exe app\\gui.py

Tabs: Start (buttons + live log), Ustawienia (length, model, premieres...), Style (on/off, research), Historia.
All logic lives in app/panel.py; this file is only the window.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import panel  # noqa: E402
from app.common import config  # noqa: E402

DAYS_PL = [("Monday", "pon"), ("Tuesday", "wt"), ("Wednesday", "śr"), ("Thursday", "czw"),
           ("Friday", "pt"), ("Saturday", "sob"), ("Sunday", "nd")]
MODEL_LABELS = {
    "acestep": [("auto", "auto - turbo (zalecane)"), ("turbo", "ACE-Step turbo 2B - szybszy"),
                ("xl-turbo", "ACE-Step XL turbo 4B - lepszy, ok. 2x wolniejszy")],
    "sa3": [("auto", "auto - medium, gdy pobrany, inaczej small"), ("medium", "medium - cały utwór naraz (do ok. 6:20)"),
            ("small-music", "small-music - sekcje po ok. 2 min")],
}
SLOWER = {"medium": ("small-music", 3), "xl-turbo": ("turbo", 2)}  # rough guess until measured
DENOISE = [("off", "wyłączone (zalecane)"), ("light", "lekkie – tylko gdy słychać syk"), ("strong", "mocniejsze – może psuć pogłos")]
MODES = [("scheduled", "scheduled - prywatny do premiery"), ("private", "private - tylko prywatny"),
         ("public", "public - od razu publiczny")]
BUTTONS = [("full", "Pełny przebieg (z uploadem)"), ("dry", "Test bez uploadu"), ("check", "Sprawdzenie"),
           ("auth", "Logowanie YouTube"), ("selftest", "Selftest"), ("repair", "Naprawa"), ("abtest", "Test jakości A/B"),
           ("report", "Raport YouTube"), ("imgbench", "Test obrazków")]


def mmss(s) -> str:
    try:
        s = int(float(s))
    except (TypeError, ValueError):
        return "-"
    return f"{s // 60}:{s % 60:02d}"


_MUTEX = None


def single_instance() -> bool:
    """Only one panel window. Windows named mutex lives as long as this process."""
    if sys.platform != "win32":
        return True
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    global _MUTEX
    _MUTEX = k32.CreateMutexW(None, False, "AIMusicAgentPanel")  # kept open until the window closes
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Music Agent")
        ico = Path(__file__).resolve().parents[1] / "assets" / "aimusic.ico"
        png = ico.with_suffix(".png")
        try:
            if sys.platform == "win32" and ico.exists():
                self.iconbitmap(default=str(ico))
            elif png.exists():
                self._icon = tk.PhotoImage(file=str(png)).subsample(8)
                self.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self.geometry("1180x780")
        self.minsize(980, 640)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Big.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Small.TLabel", foreground="#666")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        self.S: dict = {}
        self.log_next = 0
        self.form_loaded = False
        self.excluded: set[str] = set()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        self.after(400, self.poll_log)
        self.after(600, self.poll_progress)
        self.after(8000, self.auto_refresh)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="AI Music Agent", style="Big.TLabel").pack(side="left")
        self.state_lbl = tk.Label(top, text="...", padx=10, pady=2, bg="#e9ecef", font=("Segoe UI", 9, "bold"))
        self.state_lbl.pack(side="left", padx=12)
        for what, label in (("workspace", "Workspace"), ("config", "Konfiguracja"), ("logs", "Logi"),
                            ("output", "Folder z wynikami")):
            ttk.Button(top, text=label, command=lambda w=what: self.open(w)).pack(side="right", padx=2)

        self.info = ttk.Frame(self, padding=(12, 2, 12, 6))
        self.info.pack(fill="x")
        self.info_vals: dict[str, ttk.Label] = {}
        fields = [("slot", "Najbliższa premiera"), ("model", "Model muzyczny"), ("yt", "Kanał YouTube"),
                  ("videos", "Filmy / wyświetlenia"), ("free", "Wolne miejsce"), ("speed", "Szybkość silnika"),
                  ("medium", "Silnik muzyczny")]
        for i, (key, label) in enumerate(fields):
            box = ttk.Frame(self.info, padding=(8, 4), relief="groove")
            box.grid(row=0, column=i, sticky="nsew", padx=3)
            self.info.columnconfigure(i, weight=1)
            v = ttk.Label(box, text="-", font=("Segoe UI", 10, "bold"))
            v.pack(anchor="w")
            ttk.Label(box, text=label, style="Small.TLabel").pack(anchor="w")
            self.info_vals[key] = v
        self.todo = tk.Label(self, text="", bg="#fff4e6", fg="#7a3e00", justify="left", anchor="w",
                             font=("Consolas", 9), padx=8, pady=6)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.nb = nb
        nb.add(self._tab_start(nb), text="  Start  ")
        nb.add(self._tab_settings(nb), text="  Ustawienia  ")
        nb.add(self._tab_styles(nb), text="  Style  ")
        nb.add(self._tab_history(nb), text="  Historia  ")

    def _tab_start(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=10)
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.run_btns: dict[str, ttk.Button] = {}
        for key, label in BUTTONS:
            b = ttk.Button(row, text=label, command=lambda k=key: self.run(k),
                           style="Accent.TButton" if key == "full" else "TButton")
            b.pack(side="left", padx=(0, 6))
            self.run_btns[key] = b
        mb = ttk.Button(row, text="Long Mix…", command=self.long_mix_dialog)
        mb.pack(side="left", padx=(0, 6))
        self.run_btns["longmix"] = mb
        self.stop_btn = ttk.Button(row, text="Zatrzymaj", command=self.stop)
        self.stop_btn.pack(side="left", padx=(12, 0))
        # ---- progress bar of the current (or last) run
        pf = ttk.Frame(f)
        pf.pack(fill="x", pady=(10, 2))
        self.prog_title = ttk.Label(pf, text="Postęp", font=("Segoe UI", 10, "bold"))
        self.prog_title.pack(anchor="w")
        self.prog_bar = ttk.Progressbar(pf, orient="horizontal", mode="determinate", maximum=100)
        self.prog_bar.pack(fill="x", pady=3)
        self.prog_step = ttk.Label(pf, text="Nic nie działa.")
        self.prog_step.pack(anchor="w")
        self.prog_steps = ttk.Label(pf, text="", style="Small.TLabel")
        self.prog_steps.pack(anchor="w")
        self.job_lbl = ttk.Label(f, text="", style="Small.TLabel")
        self.job_lbl.pack(fill="x", pady=(6, 4))
        self.log = ScrolledText(f, wrap="word", bg="#0f1320", fg="#d8dee9", insertbackground="#d8dee9",
                                font=("Consolas", 9), relief="flat", padx=8, pady=6)
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("e", foreground="#ff8787")
        self.log.tag_configure("w", foreground="#ffd43b")
        self.log.tag_configure("h", foreground="#91a7ff")
        self.log.configure(state="disabled")
        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(6, 0))
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="przewijaj na bieżąco", variable=self.follow).pack(side="left")
        ttk.Button(bottom, text="Wyczyść log", command=self.clear_log).pack(side="right")
        return f

    def _tab_settings(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=14)
        self.v = {
            "min_seconds": tk.IntVar(), "max_seconds": tk.IntVar(), "variants": tk.IntVar(),
            "model": tk.StringVar(), "publish_mode": tk.StringVar(), "publish_time_local": tk.StringVar(),
            "shorts_enabled": tk.BooleanVar(), "shorts_seconds": tk.IntVar(), "no_repeat_substyle": tk.IntVar(),
            "max_family_streak": tk.IntVar(), "search_per_run": tk.IntVar(), "expected_handle": tk.StringVar(),
        }
        self.day_vars = {d: tk.BooleanVar() for d, _ in DAYS_PL}
        r = 0

        def head(text):
            nonlocal r
            ttk.Label(f, text=text, font=("Segoe UI", 10, "bold")).grid(row=r, column=0, columnspan=4, sticky="w", pady=(10 if r else 0, 4))
            r += 1

        def field(label, widget, hint=""):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3)
            widget.grid(row=r, column=1, sticky="w", pady=3)
            if hint:
                ttk.Label(f, text=hint, style="Small.TLabel").grid(row=r, column=2, columnspan=2, sticky="w", padx=10)
            r += 1

        def spin(var, lo, hi, step=1, width=8):
            return ttk.Spinbox(f, from_=lo, to=hi, increment=step, textvariable=var, width=width)

        head("Utwór")
        self.len_hint = ttk.Label(f, text="", style="Small.TLabel")
        field("Minimalna długość (s)", spin(self.v["min_seconds"], 60, 600, 5))
        field("Maksymalna długość (s)", spin(self.v["max_seconds"], 60, 600, 5))
        self.len_hint.grid(row=r, column=1, columnspan=3, sticky="w")
        r += 1
        self.models = MODEL_LABELS["acestep"]
        self.model_cb = ttk.Combobox(f, state="readonly", width=44, values=[t for _, t in self.models])
        field("Model muzyczny", self.model_cb)
        field("Liczba wersji do wyboru", spin(self.v["variants"], 1, 5), "mniej = szybciej")
        self.dn_cb = ttk.Combobox(f, state="readonly", width=44, values=[t for _, t in DENOISE])
        field("Czyszczenie szumu", self.dn_cb, "działa od następnego utworu")
        self.est = ttk.Label(f, text="", foreground="#3b5bdb", wraplength=760, justify="left")
        self.est.grid(row=r, column=0, columnspan=4, sticky="w", pady=(2, 4))
        r += 1

        head("Publikacja")
        field("Kanał docelowy", ttk.Entry(f, textvariable=self.v["expected_handle"], width=24),
              "np. @YourChannel – na inny kanał agent nic nie wyśle (puste = bez sprawdzania)")
        days = ttk.Frame(f)
        for d, lbl in DAYS_PL:
            ttk.Checkbutton(days, text=lbl, variable=self.day_vars[d]).pack(side="left", padx=(0, 8))
        field("Dni premier", days)
        field("Godzina premiery", ttk.Entry(f, textvariable=self.v["publish_time_local"], width=8), "GG:MM, czas polski")
        self.mode_cb = ttk.Combobox(f, state="readonly", width=44, values=[t for _, t in MODES])
        field("Tryb publikacji", self.mode_cb)
        field("Short po premierze", ttk.Checkbutton(f, variable=self.v["shorts_enabled"]))
        field("Długość Shorta (s)", spin(self.v["shorts_seconds"], 15, 59))

        head("Rotacja stylów")
        field("Styl nie wraca przez (premier)", spin(self.v["no_repeat_substyle"], 0, 20))
        field("Ta sama rodzina max pod rząd", spin(self.v["max_family_streak"], 1, 5))
        field("Research stylów na przebieg", spin(self.v["search_per_run"], 0, 30),
              "1 zapytanie = 100 jednostek z 10 000 dziennego limitu YouTube API")

        bar = ttk.Frame(f)
        bar.grid(row=r, column=0, columnspan=4, sticky="w", pady=(14, 0))
        ttk.Button(bar, text="Zapisz ustawienia", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(bar, text="Cofnij zmiany", command=lambda: self.fill_form(force=True)).pack(side="left", padx=6)
        self.msg = ttk.Label(bar, text="")
        self.msg.pack(side="left", padx=10)
        r += 1
        ttk.Label(f, text="Wyłączanie stylów – zakładka Style. Pozostałe ustawienia: config\\config.yaml (przycisk Konfiguracja).",
                  style="Small.TLabel").grid(row=r, column=0, columnspan=4, sticky="w", pady=(10, 0))
        for var in (self.v["min_seconds"], self.v["max_seconds"], self.v["variants"]):
            var.trace_add("write", lambda *_: self.estimate())
        self.model_cb.bind("<<ComboboxSelected>>", lambda e: self.estimate())
        return f

    def _tab_styles(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=10)
        ttk.Label(f, text="Dwuklik albo spacja = włącz / wyłącz styl. Popyt = mediana wyświetleń filmów danego stylu "
                          "z ostatnich 30 dni (wyszukiwarka YouTube); pusty = jeszcze nie badany.",
                  style="Small.TLabel", wraplength=1000).pack(anchor="w", pady=(0, 6))
        cols = ("use", "style", "bpm", "demand", "uses", "views")
        tv = ttk.Treeview(f, columns=cols, show="tree headings", selectmode="browse")
        for c, t, w, a in (("use", "Użyj", 60, "center"), ("style", "Styl", 220, "w"), ("bpm", "Tempo", 100, "w"),
                           ("demand", "Popyt YT (mediana wyświetleń)", 260, "w"), ("uses", "Użyty", 70, "e"), ("views", "Śr. wyświetleń", 110, "e")):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor=a, stretch=(c == "demand"))
        tv.column("#0", width=110, stretch=False)
        tv.heading("#0", text="Rodzina")
        sb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        tv.tag_configure("off", foreground="#999")
        tv.bind("<Double-1>", self.toggle_style)
        tv.bind("<space>", self.toggle_style)
        self.tv_styles = tv
        side = ttk.Frame(f, padding=(10, 0))
        side.pack(side="left", fill="y")
        ttk.Button(side, text="Zapisz style", style="Accent.TButton", command=self.save).pack(fill="x")
        ttk.Button(side, text="Włącz wszystkie", command=self.all_styles_on).pack(fill="x", pady=6)
        self.style_msg = ttk.Label(side, text="", wraplength=160)
        self.style_msg.pack(fill="x")
        return f

    def _tab_history(self, nb) -> ttk.Frame:
        f = ttk.Frame(nb, padding=10)
        ttk.Label(f, text="Dwuklik = otwórz film na YouTube (albo folder z plikami, gdy nie był wysłany).",
                  style="Small.TLabel").pack(anchor="w", pady=(0, 6))
        cols = ("date", "title", "style", "min", "result")
        tv = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("date", "Data", 110), ("title", "Tytuł / błąd", 560), ("style", "Styl", 150),
                        ("min", "Minut", 70), ("result", "Wynik", 140)):
            tv.heading(c, text=t)
            tv.column(c, width=w, stretch=(c == "title"))
        tv.tag_configure("bad", foreground="#c92a2a")
        tv.bind("<Double-1>", self.open_run)
        tv.pack(fill="both", expand=True)
        self.tv_runs = tv
        return f

    # ------------------------------------------------------------------ data -> window
    def refresh(self) -> None:
        try:
            self.S = panel.status()
        except Exception as e:  # noqa: BLE001
            self.set_state(f"Błąd odczytu: {e}", "#ffe3e3")
            return
        s, j = self.S, self.S["job"]
        if j["running"]:
            self.set_state(f"Działa: {j['name']} ({j['elapsed_min']} min)", "#fff3bf")
        elif s["external_run"]:
            self.set_state("Agent działa w tle (Harmonogram / skrót)", "#fff3bf")
        elif j["exit_code"] is not None:
            self.set_state(f"{j['name']}: " + ("OK" if j["exit_code"] == 0 else f"błąd (kod {j['exit_code']})"),
                           "#d3f9d8" if j["exit_code"] == 0 else "#ffe3e3")
        else:
            self.set_state("Bezczynny", "#e9ecef")
        busy = j["running"]
        for k, b in self.run_btns.items():
            b.state(["disabled"] if busy or (s["external_run"] and k in ("full", "dry")) else ["!disabled"])
        self.stop_btn.state(["!disabled"] if busy else ["disabled"])
        self.job_lbl.configure(text=f"Workspace: {s['workspace']}")
        speed = ", ".join(f"{m}: {v:.1f} s / 1 s muzyki" for m, v in (s.get("speed") or {}).items()) or "jeszcze nie mierzona"
        chn = s.get("channel") or {}
        if s["youtube_login"]:
            yt = (chn.get("handle") or chn.get("title") or "zalogowano") if chn else "zalogowano (kanał nieznany)"
            exp = (s["settings"].get("expected_handle") or "").lower().lstrip("@")
            if exp and chn and (chn.get("handle") or "").lower().lstrip("@") != exp and chn.get("id", "").lower() != exp:
                yt = "ZŁY KANAŁ: " + yt
            au = s.get("auth") or {}
            if au.get("ok") is False:
                yt = "WYGASŁO – agent sam otworzy logowanie przy starcie"
            elif au.get("checked"):
                yt += f" – OK ({str(au['checked'])[:16].replace('T', ' ')})"
        else:
            yt = "brak logowania" if s["client_secret"] else "brak client_secret.json"
        vals = {"slot": s["next_slot"], "model": s["model"] + (" (auto)" if s["model_setting"] == "auto" else ""),
                "yt": yt, "videos": f"{s['videos']} / {s['total_views']}", "free": f"{s['free_gb']} GB",
                "speed": speed, "medium": ("ACE-Step 1.5" if s.get("engine") == "acestep" else "Stable Audio 3")
                + " (" + (", ".join(m for m, ok in (s.get("installed") or {}).items() if ok) or "brak modeli") + ")"}
        for k, v in vals.items():
            self.info_vals[k].configure(text=v)
        if s["todo"]:
            self.todo.configure(text="DO POBRANIA RECZNIE:\n" + s["todo"].strip())
            self.todo.pack(fill="x", padx=12, before=self.nb)
        else:
            self.todo.pack_forget()
        self.fill_runs()
        self.fill_styles()
        self.fill_form()

    def set_state(self, text: str, color: str) -> None:
        self.state_lbl.configure(text=text, bg=color)

    def fill_form(self, force: bool = False) -> None:
        if self.form_loaded and not force:
            self.estimate()
            return
        st = self.S["settings"]
        for k, var in self.v.items():
            if k in st and k not in ("model", "publish_mode"):
                var.set(st[k])
        self.models = MODEL_LABELS.get(self.S.get("engine", "acestep"), MODEL_LABELS["acestep"])
        self.model_cb.configure(values=[t for _, t in self.models])
        keys = [m for m, _ in self.models]
        self.model_cb.current(keys.index(st["model"]) if st["model"] in keys else 0)
        self.mode_cb.current([m for m, _ in MODES].index(st["publish_mode"]) if st["publish_mode"] in [m for m, _ in MODES] else 0)
        keys = [k for k, _ in DENOISE]
        self.dn_cb.current(keys.index(st.get("denoise")) if st.get("denoise") in keys else 0)
        for d, var in self.day_vars.items():
            var.set(d in st["premieres"])
        self.excluded = set(st["exclude"])
        self.form_loaded = True
        self.fill_styles()
        self.estimate()

    def fill_runs(self) -> None:
        tv = self.tv_runs
        tv.delete(*tv.get_children())
        for i, r in enumerate(self.S["runs"]):
            title = r["title"] if r["ok"] else (r["error"] or "")[:200]
            result = ("film" if r["url"] else "OK") if r["ok"] else "błąd"
            if r["dry"]:
                result += " (test)"
            tv.insert("", "end", iid=str(i), values=(r["date"], title, r["style"] or "", r["minutes"] or "", result),
                      tags=() if r["ok"] else ("bad",))

    def fill_styles(self) -> None:
        if not self.S:
            return
        tv = self.tv_styles
        sel = tv.selection()
        tv.delete(*tv.get_children())
        rows = self.S["styles"]
        mx = max([x["demand"] or 0 for x in rows] + [1])
        fam = None
        for x in rows:
            on = x["substyle"] not in self.excluded and x["family"] not in self.excluded
            bar = ""
            if x["demand"] is not None:
                n = round(10 * (x["demand"] / mx) ** 0.5)
                bar = f"{x['demand']:>9,}".replace(",", " ") + "   " + "|" * max(1, n)
            tv.insert("", "end", iid=x["substyle"], text=x["family"] if x["family"] != fam else "",
                      values=("tak" if on else "-", x["substyle"], "-".join(map(str, x["bpm"])) + " bpm" if x["bpm"] else "",
                              bar, x["uses"], x["avg_views"] if x["avg_views"] is not None else "-"),
                      tags=() if on else ("off",))
            fam = x["family"]
        if sel and tv.exists(sel[0]):
            tv.selection_set(sel[0])

    def estimate(self) -> None:
        if not self.S:
            return
        try:
            mn, mx, n = self.v["min_seconds"].get(), self.v["max_seconds"].get(), max(1, self.v["variants"].get())
        except (tk.TclError, ValueError):
            return
        self.len_hint.configure(text=f"Utwór od {mmss(mn)} do {mmss(mx)}")
        sel = self.models[self.model_cb.current()][0] if self.model_cb.current() >= 0 else "auto"
        inst = self.S.get("installed") or {}
        if sel != "auto":
            model = sel
        elif self.S.get("engine") == "sa3":
            model = "medium" if inst.get("medium") else "small-music"
        else:
            model = "turbo" if inst.get("turbo", True) else "xl-turbo"
        sp = self.S.get("speed") or {}
        per, src = sp.get(model), "zmierzone na tym komputerze"
        base, mult = SLOWER.get(model, (None, 1))
        if not per and base and sp.get(base):
            per, src = sp[base] * mult, f"zgrubnie {mult}× {base} – {model} jeszcze nie mierzony"
        b = self.S.get("bench") or {}
        bench = b.get("music_sec_per_audio_sec") or ((b.get("sa3_10s_audio_sec") or 0) / 10)
        if not per and bench:
            per, src = bench * mult, "z testu w setupie"
        if not per:
            self.est.configure(text="Szacunek czasu pojawi się po pierwszym przebiegu (agent mierzy szybkość silnika).")
            return
        avg = (mn + mx) / 2
        m = round(per * avg * 1.03 * n / 60)
        h = f" ({m / 60:.1f} h)" if m >= 60 else ""
        self.est.configure(text=f"Model {model}: ok. {m} min samej generacji muzyki{h} – {n} × utwór ~{mmss(avg)}; {src}.")

    # ------------------------------------------------------------------ actions
    def run(self, key: str) -> None:
        if key == "full" and not messagebox.askyesno("AI Music Agent", "Uruchomić pełny przebieg z uploadem na YouTube?"):
            return
        lock = config.paths().state / "run.lock"
        if key in ("full", "dry") and lock.exists() and time.time() - lock.stat().st_mtime < 10 * 3600:
            messagebox.showwarning("AI Music Agent", "Agent już działa (np. z Harmonogramu). Poczekaj na koniec.")
            return
        err = panel.JOB.start(key)
        if err:
            messagebox.showwarning("AI Music Agent", err)
        self.nb.select(0)
        self.refresh()

    def long_mix_dialog(self) -> None:
        """Choose style family + length; shows how many published tracks with audio are available."""
        inv = (self.S or {}).get("mix_inventory") or {}
        names = {"all": "wszystkie style", "chill": "chill / lofi", "classical": "klasyka / pianino", "electronic": "elektronika",
                 "cinematic": "filmowa", "ambient": "ambient", "acoustic": "akustyczna"}
        keys = ["all", "chill", "classical", "electronic", "cinematic", "ambient", "acoustic"]
        win = tk.Toplevel(self)
        win.title("Long Mix")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=16)
        f.pack(fill="both")
        ttk.Label(f, text="Długi miks z utworów już opublikowanych na kanale (bez generowania muzyki).",
                  wraplength=420).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        labels = [f"{names[k]} – {inv.get(k, {}).get('tracks', 0)} utw., {inv.get(k, {}).get('minutes', 0):.0f} min" for k in keys]
        ttk.Label(f, text="Styl").grid(row=1, column=0, sticky="w")
        fam = ttk.Combobox(f, state="readonly", width=44, values=labels)
        fam.current(0)
        fam.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Długość (godziny)").grid(row=2, column=0, sticky="w")
        hrs = tk.DoubleVar(value=1.0)
        ttk.Spinbox(f, from_=1, to=3, increment=0.5, textvariable=hrs, width=6).grid(row=2, column=1, sticky="w", pady=3)
        dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Bez wysyłki (tylko przygotuj plik do obejrzenia)", variable=dry).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        info = ttk.Label(f, text="", foreground="#c92a2a", wraplength=420)
        info.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def check(*_):
            k = keys[max(0, fam.current())]
            try:
                need = float(hrs.get()) * 60
            except (tk.TclError, ValueError):
                return False
            have = float(inv.get(k, {}).get("minutes", 0))
            info.configure(text="" if have >= need * 0.97 else
                           f"Za mało muzyki: {have:.0f} min z {need:.0f}. Wybierz inny styl, krótszy miks albo poczekaj na więcej utworów "
                           "(1 godzina = ok. 15 utworów).")
            return have >= need * 0.97

        fam.bind("<<ComboboxSelected>>", check)
        hrs.trace_add("write", check)
        check()

        def go():
            if not check():
                return
            k = keys[max(0, fam.current())]
            extra = ["--family", k, "--hours", f"{float(hrs.get()):g}"] + (["--dry-run"] if dry.get() else [])
            win.destroy()
            err = panel.JOB.start("longmix", extra)
            if err:
                messagebox.showwarning("AI Music Agent", err)
            self.nb.select(0)
            self.refresh()

        bar = ttk.Frame(f)
        bar.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Anuluj", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Utwórz Long Mix", style="Accent.TButton", command=go).pack(side="right", padx=6)

    def stop(self) -> None:
        if messagebox.askyesno("AI Music Agent", "Zatrzymać? Gotowe pliki zostaną – kolejny start dokończy resztę."):
            panel.JOB.stop()
            self.refresh()

    def poll_progress(self) -> None:
        try:
            d = panel.progress()
        except Exception:  # noqa: BLE001
            d = None
        if not d:
            self.after(1000, self.poll_progress)
            return
        pct = int(round(float(d.get("overall") or 0) * 100))
        self.prog_bar["value"] = pct
        icons = {"done": "✓", "running": "▶", "todo": "·", "skipped": "–"}
        self.prog_steps.configure(text="   ".join(f"{icons.get(x['state'], '·')} {x['label']}" for x in d.get("steps", [])))
        el = mmss(d.get("elapsed_sec") or 0)
        if d.get("finished"):
            self.prog_title.configure(text=f"Ostatni przebieg: {'zakończony' if d.get('ok') else 'przerwany'} ({pct}%)")
            self.prog_step.configure(text=f"Czas: {el} min   {d.get('message') or ''}"[:160])
        elif d.get("stale"):
            self.prog_title.configure(text=f"Przebieg przerwany? Brak postępu od ponad 15 min ({pct}%)")
            self.prog_step.configure(text=f"Ostatni etap: {d.get('label')} {d.get('detail') or ''}")
        else:
            eta = int(d.get("eta_sec") or 0)
            left = f"{eta // 3600} h {eta % 3600 // 60:02d} min" if eta >= 3600 else f"{max(1, round(eta / 60))} min"
            self.prog_title.configure(text=f"Postęp: {pct}%   –   zostało ok. {left} (koniec ok. {d.get('eta_at')})")
            what = d.get("label") or ""
            if d.get("detail"):
                what += f" – {d['detail']}"
            self.prog_step.configure(text=f"Teraz: {what}   (trwa już {el} min)")
        self.after(1000, self.poll_progress)

    def poll_log(self) -> None:
        nxt, lines = panel.JOB.since(self.log_next)
        self.log_next = nxt
        if lines:
            self.log.configure(state="normal")
            for line in lines:
                tag = ("e" if any(x in line for x in ("ERROR", "STOPPED", "FAIL", "Traceback")) else
                       "w" if ("WARNING" in line or "note:" in line) else "h" if line.startswith("=====") else "")
                self.log.insert("end", line + "\n", (tag,) if tag else ())
            if int(self.log.index("end-1c").split(".")[0]) > 5000:
                self.log.delete("1.0", "1000.0")
            self.log.configure(state="disabled")
            if self.follow.get():
                self.log.see("end")
            if any(line.startswith(("===== koniec", "===== zatrzymano")) for line in lines):
                self.refresh()
        self.after(500, self.poll_log)

    def auto_refresh(self) -> None:
        if self.nb.index("current") != 1:  # do not disturb while editing settings
            self.refresh()
        self.after(8000, self.auto_refresh)

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def toggle_style(self, _event=None) -> None:
        sel = self.tv_styles.selection() or (self.tv_styles.focus(),)
        sub = sel[0] if sel else ""
        if not sub:
            return
        fam = next((x["family"] for x in self.S["styles"] if x["substyle"] == sub), None)
        if sub in self.excluded or fam in self.excluded:
            self.excluded.discard(sub)
            if fam in self.excluded:  # a whole family was excluded in config.yaml -> switch to single styles
                self.excluded.discard(fam)
                self.excluded |= {x["substyle"] for x in self.S["styles"] if x["family"] == fam and x["substyle"] != sub}
        else:
            self.excluded.add(sub)
        self.style_msg.configure(text="Niezapisane zmiany – kliknij Zapisz style.")
        self.fill_styles()

    def all_styles_on(self) -> None:
        self.excluded.clear()
        self.style_msg.configure(text="Niezapisane zmiany – kliknij Zapisz style.")
        self.fill_styles()

    def save(self) -> None:
        try:
            data = {k: var.get() for k, var in self.v.items() if k not in ("model", "publish_mode")}
        except (tk.TclError, ValueError):
            messagebox.showerror("AI Music Agent", "W jednym z pól liczbowych nie ma liczby.")
            return
        data["model"] = self.models[max(0, self.model_cb.current())][0]
        data["publish_mode"] = MODES[max(0, self.mode_cb.current())][0]
        data["denoise"] = DENOISE[self.dn_cb.current() if self.dn_cb.current() >= 0 else 0][0]
        data["publish_time_local"] = str(data["publish_time_local"]).strip()
        data["premieres"] = [d for d, var in self.day_vars.items() if var.get()]
        data["exclude"] = sorted(self.excluded)
        try:
            msg = panel.save_settings(data)
        except ValueError as e:
            self.msg.configure(text=str(e), foreground="#c92a2a")
            self.style_msg.configure(text=str(e), foreground="#c92a2a")
            messagebox.showerror("AI Music Agent", str(e))
            return
        except Exception as e:  # noqa: BLE001 - e.g. config.yaml locked by an editor
            messagebox.showerror("AI Music Agent", f"Nie udało się zapisać: {e}")
            return
        self.msg.configure(text=msg, foreground="#2f9e44")
        self.style_msg.configure(text="Zapisano.", foreground="#2f9e44")
        self.form_loaded = False
        self.refresh()

    def open(self, what: str) -> None:
        try:
            panel.open_target(what)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("AI Music Agent", str(e))

    def open_run(self, _event=None) -> None:
        sel = self.tv_runs.selection()
        if not sel:
            return
        r = self.S["runs"][int(sel[0])]
        self.open(r["url"] or r["folder"])

    def on_close(self) -> None:
        if panel.JOB.running():
            if not messagebox.askyesno("AI Music Agent", f"Trwa: {panel.JOB.name}.\nZatrzymać i zamknąć okno?"):
                return
            panel.JOB.stop()
        self.destroy()


def machine_env() -> None:
    """Started from a desktop shortcut right after setup, Explorer may not know the new variables yet.
    Read them from the registry (same as AIMusicAgent.exe does) - jobs started here inherit them."""
    if sys.platform != "win32":
        return
    import os
    import winreg
    prefixes = ("AIMUSIC_", "OLLAMA_", "SA3_", "HF_HOME", "PIP_CACHE_DIR")
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            if name.upper().startswith(prefixes) and isinstance(value, str):
                os.environ[name] = os.path.expandvars(value)
    except OSError:
        pass
    os.environ.setdefault("AIMUSIC_HOME", str(Path(__file__).resolve().parents[1]))


def main() -> None:
    machine_env()
    if sys.platform == "win32":
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    if not single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("AI Music Agent", "Panel jest już otwarty (sprawdź pasek zadań).")
        return
    config.paths().ensure()
    App().mainloop()


if __name__ == "__main__":
    main()
