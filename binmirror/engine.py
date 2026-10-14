"""Mette insieme disco, Resolve e piano: analyze(), apply() e la modalità automatica."""
import json
import os

from . import disk as diskmod
from . import planner
from . import resolve_bridge as rb

DISK_ORDER = {"mkdir": 0, "move": 1, "trash": 2}
RES_ORDER = {"create_bin": 0, "move_bin": 1, "move_clip": 2, "relink": 3, "update_seq": 4,
             "import": 5, "import_seq": 6, "import_timeline": 7, "delete_clips": 8,
             "delete_bin": 9}

# Impostazioni della singola cartella radice (stanno in <radice>/.binmirror/settings.json,
# così valgono uguali da qualsiasi Mac apra quel disco).
DEFAULT_SETTINGS = {
    "extensions": [],                       # vuota = tutte quelle che Resolve legge
    "ignore": list(diskmod.DEFAULT_IGNORE),  # nomi di cartella o /regex/
    "timelines": False,                     # importa .drt .xml .fcpxml .otio come timeline
    "date_field": "Comments",               # dove scrivere la data di import ("" = da nessuna parte)
}
DATE_FIELDS = ("Comments", "Description", "Keywords", "")


class PlanChanged(Exception):
    """Tra Analizza e Applica qualcosa è cambiato: va mostrata la nuova anteprima."""

    def __init__(self, plan):
        super().__init__("Nel frattempo qualcosa è cambiato su disco o in Resolve: "
                         "controlla di nuovo l'anteprima.")
        self.plan = plan


class Busy(Exception):
    pass


def state_path(root):
    return os.path.join(root, diskmod.STATE_DIR, "state.json")


def load_state(root):
    try:
        with open(state_path(root)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def settings_path(root):
    return os.path.join(root, diskmod.STATE_DIR, "settings.json")


def load_settings(root):
    out = dict(DEFAULT_SETTINGS)
    try:
        with open(settings_path(root)) as f:
            out.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return out


def save_settings(root, settings):
    os.makedirs(os.path.dirname(settings_path(root)), exist_ok=True)
    with open(settings_path(root), "w") as f:
        json.dump({k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}, f, indent=1)


def filters_for(settings):
    return diskmod.Filters(settings.get("extensions"), settings.get("ignore"))


def save_state(root, state):
    os.makedirs(os.path.dirname(state_path(root)), exist_ok=True)
    tmp = state_path(root) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, state_path(root))


class Lock:
    """Una sola sincronizzazione alla volta sulla stessa cartella (due finestre, due Mac…)."""

    def __init__(self, root):
        self.path = os.path.join(root, diskmod.STATE_DIR, "lock")

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path) as f:
                pid = int(f.read().strip() or 0)
            if pid and pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    raise Busy("Un'altra sincronizzazione di questa cartella è in corso.")
                except ProcessLookupError:
                    pass        # lock rimasto da un crash
                except PermissionError:
                    raise Busy("Un'altra sincronizzazione di questa cartella è in corso.")
        except (OSError, ValueError):
            pass
        with open(self.path, "w") as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def _blocked(reason):
    p = planner.Plan()
    p.blocked = reason
    return p


def analyze(root, base="", force=False, disk=None):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return _blocked(planner._missing_root_reason(root))
    st = load_settings(root)
    d = disk or diskmod.scan(root, filters_for(st))
    r = rb.snapshot(base)
    return planner.build_plan(root, d, r, load_state(root), force=force, base=base,
                              timelines=bool(st.get("timelines")))


def _sorted(actions, order):
    def key(a):
        p = a.get("path") or a.get("src") or a.get("to") or a.get("bin") or a.get("file") or ""
        group = a.get("bin", os.path.dirname(a.get("file", "")))
        depth = p.count("/")
        if a["kind"] == "delete_bin":
            depth = -depth
        # nelle mosse su disco, prima le cartelle e poi i file
        is_file = 1 if (a["kind"] == "move" and not a.get("folder")) else 0
        return (order[a["kind"]], is_file, depth, group, p)
    return sorted(actions, key=key)


def apply(root, plan, base="", selections=None, recheck=True, auto=False, disk=None):
    """Esegue il piano: prima il disco, poi Resolve (in una sola chiamata).

    recheck: rifà l'analisi e si ferma se nel frattempo è cambiato qualcosa.
    auto: solo le aggiunte (planner.AUTO_KINDS), e lo stato si aggiorna solo per quelle.
    disk: in modalità automatica, la foto del disco appena fatta (il disco non lo tocco).
    Ritorna [(azione, esito)]."""
    root = os.path.abspath(root)
    if plan.blocked:
        raise RuntimeError(plan.blocked)
    with Lock(root):
        if recheck:
            fresh = analyze(root, base=base, force=plan.forced)
            if fresh.signature() != plan.signature():
                raise PlanChanged(fresh)
        if auto:
            disk_actions = []
            res_actions = [a for a in plan.resolve if a["kind"] in planner.AUTO_KINDS]
        else:
            disk_actions, res_actions = plan.selected_actions(selections)
        ops = diskmod.DiskOps(root)
        report = []
        actual = {}   # percorso previsto → percorso reale (se c'era già un file con quel nome)
        for a in _sorted(disk_actions, DISK_ORDER):
            try:
                if a["kind"] == "mkdir":
                    out = ops.mkdir(a["path"])
                elif a["kind"] == "move":
                    out = ops.move(a["src"], a["dst"], folder=bool(a.get("folder")))
                    if not a.get("folder") and out != a["dst"] and \
                            out not in ("già fatto", "sorgente sparita"):
                        actual[a["dst"]] = out      # c'era già un file con quel nome: _1
                else:
                    out = ops.trash(a["path"])
            except OSError as e:
                out = "errore: %s" % e
            report.append((a, out))

        pairs = []
        for a in _sorted(res_actions, RES_ORDER):
            b = {k: v for k, v in a.items() if k != "why"}
            if "file" in b:
                b["file"] = diskmod.abs_path(root, actual.get(b["file"], b["file"]))
            pairs.append((a, b))
        snap = None
        settings = load_settings(root)
        if pairs:
            results, snap = rb.apply([b for _, b in pairs], base=base,
                                     date_field=settings.get("date_field", ""))
            report += [(a, out) for (a, _), out in zip(pairs, results)]
        elif auto:
            return report
        else:
            snap = rb.snapshot(base)
        prev = load_state(root) if not plan.forced else {}
        d = disk if (auto and disk) else diskmod.scan(root, filters_for(settings))
        if auto:
            save_state(root, merge_state(root, d, snap, prev, report))
        else:
            save_state(root, current_state(root, base, snap, prev, report, d))
        return report


def _ok(out):
    return str(out).startswith("ok")


def _tl_done(root, d, prev, report):
    """Timeline già importate: quelle di prima che ci sono ancora su disco + quelle di adesso."""
    by_ino = {r["ino"]: p for p, r in d.timelines.items()}
    out = {}
    for p, r in prev.get("timelines", {}).items():
        now = p if p in d.timelines else by_ino.get(r.get("ino"))
        if now:
            out[now] = {"ino": d.timelines[now]["ino"], "fp": d.timelines[now]["fp"]}
    for a, res in report:
        if a["kind"] == "import_timeline" and _ok(res) and a["file"] in d.timelines:
            out[a["file"]] = {"ino": d.timelines[a["file"]]["ino"],
                              "fp": d.timelines[a["file"]]["fp"]}
    return out


def _seqs_now(root_real, d, snap, prev):
    """Sequenze collegate a una clip, più quelle che l'utente ha tolto da Resolve (da non
    reimportare finché esistono su disco)."""
    out = {}
    for c in snap.clips:
        k = planner.seq_clip_key(root_real, c["file"]) if c["file"] else None
        if k and k in d.sequences:
            out.setdefault(k, c["uid"])
    for k, uid in prev.get("seqs", {}).items():
        if k in d.sequences and k not in out:
            out[k] = uid
    return out


def _link(d, c, rel):
    return {"ino": d.files[rel]["ino"], "fp": d.files[rel]["fp"], "bin": c["bin"], "path": rel}


def merge_state(root, d, snap, prev, report):
    """Dopo un giro automatico: allo stato di prima aggiungo solo quello che è stato appena
    aggiunto. Il resto (una clip spostata in Resolve, un bin tolto…) resta "da fare" per la
    prossima sincronizzazione manuale, che deve ancora vederlo come cambiato."""
    root_real = os.path.realpath(root)
    st = dict(prev)
    folders, links = dict(prev.get("folders", {})), dict(prev.get("links", {}))
    for a, out in report:
        if a["kind"] == "create_bin" and _ok(out) and a["path"] in snap.bins \
                and a["path"] in d.folders:
            folders[a["path"]] = {"uid": snap.bins[a["path"]], "ino": d.folders[a["path"]]}
    new_files = {a["file"] for a, out in report if a["kind"] == "import" and _ok(out)}
    for c in snap.clips:
        rel = planner.rel_in_root(root_real, c["file"])
        if rel in new_files and rel in d.files and c["uid"] not in links:
            links[c["uid"]] = _link(d, c, rel)
    st.update(folders=folders, links=links, seqs=_seqs_now(root_real, d, snap, prev),
              timelines=_tl_done(root, d, prev, report))
    return st


def current_state(root, base, snap, prev, report=(), d=None):
    """La foto concordata: ciò che esiste da entrambe le parti, più i bin che l'utente ha
    deciso di tenere anche se la loro cartella è sparita (così non la ricreo)."""
    d = d or diskmod.scan(root, filters_for(load_settings(root)))
    root_real = os.path.realpath(root)
    folders = {p: {"uid": snap.bins[p], "ino": ino} for p, ino in d.folders.items() if p in snap.bins}
    uid_to_path = {u: p for p, u in snap.bins.items() if u}
    for p, rec in prev.get("folders", {}).items():
        now = uid_to_path.get(rec.get("uid")) if rec.get("uid") else (p if p in snap.bins else None)
        if now and now not in folders and now not in d.folders:
            folders[now] = {"uid": snap.bins[now], "ino": None, "missing": True}
    links = {}
    for c in snap.clips:
        rel = planner.rel_in_root(root_real, c["file"])
        if rel in d.files:
            links[c["uid"]] = _link(d, c, rel)
    return {"version": 3, "project": snap.project, "base": base, "folders": folders,
            "links": links, "seqs": _seqs_now(root_real, d, snap, prev),
            "timelines": _tl_done(root, d, prev, report)}


# ── riepilogo e modalità automatica ─────────────────────────────────────────

BAD = ("errore", "sorgente sparita", "lasciato", "bin non trovato", "clip non trovata",
       "azione sconosciuta", "non aggiornata", "ImportMedia", "relink fallito")


def is_bad(out):
    return str(out).startswith(BAD) or "fallito" in str(out)


def summarize(report):
    """Una riga per la notifica: «3 clip importate in SC_01, 1 in MUSICA»."""
    per_bin, other, bad = {}, 0, 0
    for a, out in report:
        if is_bad(out):
            bad += 1
        elif a["kind"] in ("import", "import_seq", "import_timeline"):
            b = a.get("bin") or "Master"
            per_bin[b] = per_bin.get(b, 0) + 1
        elif a["kind"] != "create_bin":
            other += 1
    parts = []
    for i, (b, n) in enumerate(sorted(per_bin.items(), key=lambda kv: -kv[1])):
        parts.append(("%d clip importat%s in %s" % (n, "a" if n == 1 else "e", b)) if i == 0
                     else "%d in %s" % (n, b))
    if other:
        parts.append("%d altr%s modific%s" % (other, "a" if other == 1 else "e",
                                               "a" if other == 1 else "he"))
    if bad:
        parts.append("%d da controllare" % bad)
    return ", ".join(parts) or "Nessuna modifica."


class Watcher:
    """Modalità automatica: a ogni giro guarda il disco e, se è comparso qualcosa,
    importa da solo. Spostamenti, rinomine e rimozioni restano alla sincronizzazione
    manuale con anteprima.

    tick() ritorna (stato, dettaglio):
      ("quiet", None)        niente di nuovo su disco
      ("ok", report)         ha importato (report può essere vuoto)
      ("pausa", motivo)      disco scollegato, progetto diverso, Resolve chiusa…
                             Si riprova da solo al giro dopo."""

    def __init__(self, root, base=""):
        self.root, self.base = os.path.abspath(root), base
        self.sig = None

    def tick(self):
        if not os.path.isdir(self.root):
            self.sig = None
            return "pausa", planner._missing_root_reason(self.root)
        if not os.path.isfile(state_path(self.root)):
            return "pausa", "Fai prima una sincronizzazione manuale di questa cartella."
        d = diskmod.scan(self.root, filters_for(load_settings(self.root)))
        sig = diskmod.signature(d)
        if sig == self.sig:
            return "quiet", None
        try:
            plan = analyze(self.root, base=self.base, disk=d)
        except rb.ResolveError as e:
            return "pausa", "Resolve: %s" % e
        if plan.blocked:
            return "pausa", plan.blocked
        report = []
        if any(a["kind"] in planner.AUTO_KINDS for a in plan.resolve):
            report = apply(self.root, plan, base=self.base, recheck=False, auto=True, disk=d)
        if not plan.pending:
            self.sig = sig      # con file ancora in copia si riguarda al giro dopo
        return "ok", report
