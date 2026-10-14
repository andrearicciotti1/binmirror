"""BinMirror — finestra per DaVinci Resolve.

Scegli la cartella su disco e (se vuoi) il bin di Resolve da rispecchiare,
premi Analizza, guarda l'anteprima, premi Applica.
"""
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binmirror import __version__, engine, license  # noqa: E402
from binmirror.resolve_bridge import ResolveError  # noqa: E402

WARN = "#f4a261"

APP_NAME = "BinMirror"
CONFIG = os.path.expanduser("~/.binmirror_config.json")
if sys.platform == "darwin":
    LOG = os.path.expanduser("~/Library/Logs/BinMirror.log")
else:
    LOG = os.path.expanduser("~/.binmirror.log")

ACCENT = "#e63946"
BG = "#161616"
PANEL = "#202020"
MUTED = "#8a8a8a"
TEXT = "#e8e8e8"

ICON = {"mkdir": "📁", "move": "↪", "trash": "🗑", "create_bin": "🗂", "move_bin": "↪",
        "delete_bin": "✕", "delete_clips": "✕", "move_clip": "🎞", "relink": "🔗", "import": "⬇"}


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=1)
    except OSError:
        logging.exception("config non salvata")


class BinMirrorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("%s %s" % (APP_NAME, __version__))
        self.geometry("720x620")
        self.minsize(600, 480)
        self.configure(fg_color=BG)
        self.cfg = load_config()
        self.plan = None
        self.busy = False
        self.selections = {}
        self._build()
        self._refresh_buttons()
        self.after(600, self._license_gate)

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(head, text="BinMirror", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(head, text="  bin di Resolve ⇄ cartelle su disco",
                     font=ctk.CTkFont(size=13), text_color=MUTED).pack(side="left", pady=(6, 0))

        form = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        form.pack(fill="x", padx=20, pady=8)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Cartella su disco", text_color=MUTED).grid(
            row=0, column=0, sticky="w", padx=(14, 10), pady=(14, 6))
        self.root_var = tk.StringVar(value=self.cfg.get("root", ""))
        ctk.CTkEntry(form, textvariable=self.root_var,
                     placeholder_text="es. /Volumes/SSD/MATERIALI").grid(
            row=0, column=1, sticky="ew", pady=(14, 6))
        ctk.CTkButton(form, text="Scegli…", width=90, fg_color="#3a3a3a", hover_color="#4a4a4a",
                      command=self._choose_root).grid(row=0, column=2, padx=14, pady=(14, 6))

        ctk.CTkLabel(form, text="Bin di Resolve", text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=(14, 10), pady=(6, 14))
        # Senza textvariable, così il testo d'aiuto resta visibile quando è vuoto.
        self.base_entry = ctk.CTkEntry(form, placeholder_text="vuoto = tutto il Master")
        self.base_entry.grid(row=1, column=1, sticky="ew", pady=(6, 14))
        if self.cfg.get("base"):
            self.base_entry.insert(0, self.cfg["base"])

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(6, 4))
        self.btn_analyze = ctk.CTkButton(btns, text="Analizza", width=150, height=36,
                                         fg_color="#3a3a3a", hover_color="#4a4a4a",
                                         command=self._analyze)
        self.btn_analyze.pack(side="left")
        self.btn_apply = ctk.CTkButton(btns, text="Applica", width=150, height=36,
                                       fg_color=ACCENT, hover_color="#c92f3b",
                                       command=self._apply)
        self.btn_apply.pack(side="left", padx=10)
        # Compare solo quando un blocco si può superare (progetto diverso, cestino grande).
        self.btn_force = ctk.CTkButton(btns, text="Procedi lo stesso", width=150, height=36,
                                       fg_color="transparent", border_width=1, border_color=WARN,
                                       text_color=WARN, hover_color="#2a2a2a",
                                       command=lambda: self._analyze(force=True))
        self.status = ctk.CTkLabel(btns, text="Pronto.", text_color=MUTED, anchor="w")
        self.status.pack(side="left", padx=8, fill="x", expand=True)

        # Domande (❓): una riga per decisione, con la risposta proposta già selezionata.
        self.choices_frame = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        self.out = ctk.CTkTextbox(self, fg_color=PANEL, text_color=TEXT, corner_radius=10,
                                  font=ctk.CTkFont(size=13), wrap="word")
        self.out.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        for tag, color in (("h", TEXT), ("muted", MUTED), ("warn", "#f4a261"),
                           ("ok", "#2a9d8f"), ("err", ACCENT)):
            self.out.tag_config(tag, foreground=color)
        self._write([("Scegli la cartella e premi Analizza: vedrai cosa cambierebbe "
                      "prima di toccare qualsiasi cosa.\n\nNessun file viene mai cancellato: "
                      "quello che sparisce finisce in _CESTINO dentro la cartella.", "muted")])

        ctk.CTkLabel(self, text="Serve DaVinci Resolve Studio aperto, con Preferences → System → "
                     "General → External scripting using = Local.",
                     text_color=MUTED, font=ctk.CTkFont(size=11)).pack(pady=(0, 10))

    # ── licenza Gumroad (come DropCut) ────────────────────────────────────
    def _license_gate(self):
        st = license.status(self.cfg)
        if st == "ask":
            self._license_dialog()
        elif st == "check":
            key = self.cfg["license_key"]
            threading.Thread(target=self._license_bg, args=(key,), daemon=True).start()

    def _license_bg(self, key):
        ok, msg = license.verify(key)
        if ok is True:
            self.cfg["license_last_verified"] = time.time()
            save_config(self.cfg)
        elif ok is False:
            self.after(0, lambda: self._license_dialog("Licenza non più valida: %s" % msg))
        elif license.offline_expired(self.cfg):
            self.after(0, lambda: self._license_dialog(
                "Non riesco a verificare la licenza da più di 7 giorni: %s\n"
                "Collegati a internet e riprova." % msg))
        else:
            self.after(0, lambda: self.status.configure(
                text="Licenza non verificata (offline), riprovo nei prossimi giorni."))

    def _license_dialog(self, error=""):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Attiva BinMirror")
        dlg.geometry("500x300")
        dlg.configure(fg_color=BG)
        dlg.transient(self)
        dlg.protocol("WM_DELETE_WINDOW", self.quit)
        dlg.after(100, dlg.grab_set)
        ctk.CTkLabel(dlg, text="Attiva BinMirror", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TEXT).pack(pady=(24, 6))
        ctk.CTkLabel(dlg, text="Inserisci la license key che hai ricevuto via email da Gumroad.",
                     text_color=MUTED, wraplength=440).pack(padx=24, pady=(0, 12))
        entry = ctk.CTkEntry(dlg, placeholder_text="XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX",
                             height=38, justify="center")
        entry.pack(fill="x", padx=36)
        if self.cfg.get("license_key"):
            entry.insert(0, self.cfg["license_key"])
        msg_lbl = ctk.CTkLabel(dlg, text=error, text_color=ACCENT, wraplength=440)
        msg_lbl.pack(padx=24, pady=8)

        def done(key, ok, msg):
            btn.configure(state="normal")
            if ok is True:
                self.cfg["license_key"] = key
                self.cfg["license_last_verified"] = time.time()
                save_config(self.cfg)
                dlg.destroy()
            elif ok is False:
                msg_lbl.configure(text="Licenza non valida: %s" % msg, text_color=ACCENT)
            else:
                msg_lbl.configure(text="Errore di rete: %s\nControlla la connessione." % msg,
                                  text_color=ACCENT)

        def attiva(_e=None):
            key = entry.get().strip()
            if not key:
                return
            btn.configure(state="disabled")
            msg_lbl.configure(text="Verifica in corso…", text_color=MUTED)
            threading.Thread(target=lambda: (lambda r: self.after(0, done, key, *r))(
                license.verify(key)), daemon=True).start()

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=36, pady=(4, 20))
        ctk.CTkButton(row, text="Esci", width=110, fg_color="#3a3a3a", hover_color="#4a4a4a",
                      command=self.quit).pack(side="left")
        btn = ctk.CTkButton(row, text="Attiva", width=150, fg_color=ACCENT,
                            hover_color="#c92f3b", command=attiva)
        btn.pack(side="right")
        entry.bind("<Return>", attiva)

    # ── helpers ───────────────────────────────────────────────────────────
    def _write(self, parts, clear=True):
        self.out.configure(state="normal")
        if clear:
            self.out.delete("1.0", "end")
        for text, tag in parts:
            self.out.insert("end", text, tag)
        self.out.configure(state="disabled")

    def _choose_root(self):
        p = filedialog.askdirectory(title="Cartella da rispecchiare",
                                    initialdir=self.root_var.get() or os.path.expanduser("~"))
        if p:
            self.root_var.set(p)
            self.plan = None
            self._refresh_buttons()

    def _refresh_buttons(self):
        idle = "normal" if not self.busy else "disabled"
        self.btn_analyze.configure(state=idle)
        can = (not self.busy and self.plan is not None and not self.plan.blocked
               and not self.plan.is_empty())
        self.btn_apply.configure(state="normal" if can else "disabled")
        force = (not self.busy and self.plan is not None and self.plan.blocked
                 and self.plan.can_force)
        if force:
            self.btn_force.pack(side="left", after=self.btn_apply)
        else:
            self.btn_force.pack_forget()

    def _inputs(self):
        root = self.root_var.get().strip()
        base = self.base_entry.get().strip().strip("/")
        self.cfg.update(root=root, base=base)
        save_config(self.cfg)
        return root, base

    def _run(self, label, work, done):
        """Lavoro in background (Resolve può essere lenta), risultato sul thread Tk."""
        self.busy = True
        self.status.configure(text=label)
        self._refresh_buttons()

        def bg():
            try:
                res, err = work(), None
            except engine.PlanChanged as e:
                res, err = e, None
            except (ResolveError, engine.Busy) as e:
                res, err = None, ("Resolve: %s" % e) if isinstance(e, ResolveError) else str(e)
            except Exception as e:  # noqa: BLE001
                logging.exception("errore")
                res, err = None, str(e)
            self.after(0, lambda: self._finish(done, res, err))
        threading.Thread(target=bg, daemon=True).start()

    def _finish(self, done, res, err):
        self.busy = False
        if err:
            self.plan = None
            self.status.configure(text="Errore.")
            hint = ""
            if "non raggiungibile" in err:
                hint = ("\n\nApri DaVinci Resolve Studio con un progetto. Se è già aperta, "
                        "controlla Preferences → System → General → External scripting "
                        "using = Local e riavvia Resolve.")
            elif "Nessun progetto" in err:
                hint = "\n\nApri un progetto in Resolve e riprova."
            self._write([("⛔ " + err + hint, "err")])
        else:
            done(res)
        self._refresh_buttons()

    # ── azioni ────────────────────────────────────────────────────────────
    def _analyze(self, force=False):
        root, base = self._inputs()
        if not root:
            self._write([("Scegli prima la cartella su disco.", "warn")])
            return
        self._run("Analizzo…", lambda: engine.analyze(root, base=base, force=force),
                  self._show_plan)

    def _show_choices(self, plan):
        for w in self.choices_frame.winfo_children():
            w.destroy()
        self.selections = {}
        if not plan.choices or plan.blocked:
            self.choices_frame.pack_forget()
            return
        ctk.CTkLabel(self.choices_frame, text="DA DECIDERE", text_color=WARN, anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(fill="x", padx=14, pady=(10, 0))
        for c in plan.choices:
            ctk.CTkLabel(self.choices_frame, text="❓ " + c["question"], text_color=TEXT,
                         anchor="w", justify="left", wraplength=640).pack(fill="x", padx=14,
                                                                           pady=(8, 4))
            labels = {o["label"]: o["key"] for o in c["options"]}
            default = [o["label"] for o in c["options"] if o["key"] == c["default"]][0]
            self.selections[c["id"]] = c["default"]

            def pick(label, cid=c["id"], labels=labels):
                self.selections[cid] = labels[label]
            seg = ctk.CTkSegmentedButton(self.choices_frame, values=list(labels), command=pick,
                                         selected_color=ACCENT, selected_hover_color="#c92f3b")
            seg.set(default)
            seg.pack(anchor="w", padx=14, pady=(0, 6))
        self.choices_frame.pack(fill="x", padx=20, pady=(8, 0), before=self.out)

    def _show_plan(self, plan):
        if isinstance(plan, engine.PlanChanged):
            self._show_plan(plan.plan)
            self._write([("⚠  " + str(plan) + "\n", "warn")], clear=False)
            return
        self.plan = plan
        self._show_choices(plan)
        parts = []
        if plan.blocked:
            self.status.configure(text="Bloccato.")
            parts.append(("⛔ " + plan.blocked + "\n", "err"))
        elif plan.is_empty():
            self.status.configure(text="Tutto allineato.")
            parts.append(("✓ Disco e Resolve sono già allineati.\n", "ok"))
        else:
            n = len(plan.disk) + len(plan.resolve)
            q = len(plan.choices)
            parts_txt = []
            if n:
                parts_txt.append("%d modific%s" % (n, "a" if n == 1 else "he"))
            if q:
                parts_txt.append("%d decision%s da prendere" % (q, "e" if q == 1 else "i"))
            self.status.configure(text=" e ".join(parts_txt) + ".")
        for title, actions in (("SU DISCO", plan.disk), ("IN RESOLVE", plan.resolve)):
            if actions and not plan.blocked:
                parts.append(("\n%s\n" % title, "h"))
                for a in actions:
                    parts.append(("  %s  %s\n" % (ICON.get(a["kind"], "•"), a["why"]), None))
        if plan.notes:
            parts.append(("\nDA SAPERE\n", "h"))
            for n in plan.notes:
                parts.append(("  ⚠  %s\n" % n, "warn"))
        self._write(parts)

    def _apply(self):
        root, base = self._inputs()
        plan, sel = self.plan, dict(self.selections)
        self._run("Applico…", lambda: engine.apply(root, plan, base=base, selections=sel),
                  self._show_report)

    def _show_report(self, report):
        if isinstance(report, engine.PlanChanged):
            self._show_plan(report)
            return
        self.plan = None
        self._show_choices(engine.planner.Plan())
        bad = [r for r in report if str(r[1]).startswith(
            ("errore", "sorgente sparita", "lasciato", "bin non trovato", "clip non trovata",
             "azione sconosciuta")) or "fallito" in str(r[1])]
        parts = [("✓ Fatto.\n" if not bad else "Fatto, con %d avvisi.\n" % len(bad),
                  "ok" if not bad else "warn")]
        for a, out in report:
            tag = "warn" if (a, out) in bad else "muted"
            parts.append(("  %s  %s → %s\n" % (ICON.get(a["kind"], "•"), a["why"], out), tag))
        self._write(parts)
        self.status.configure(text="Sincronizzato.")


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    logging.basicConfig(filename=LOG, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("BinMirror %s avviato", __version__)
    BinMirrorApp().mainloop()


if __name__ == "__main__":
    main()
