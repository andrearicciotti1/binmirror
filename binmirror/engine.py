"""Mette insieme disco, Resolve e piano: analyze() e apply()."""
import json
import os

from . import disk as diskmod
from . import planner
from . import resolve_bridge as rb

DISK_ORDER = {"mkdir": 0, "move": 1, "trash": 2}
RES_ORDER = {"create_bin": 0, "move_bin": 1, "move_clip": 2, "relink": 3, "import": 4,
             "delete_clips": 5, "delete_bin": 6}


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


def analyze(root, base="", force=False):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        p = planner.Plan()
        p.blocked = planner._missing_root_reason(root)
        return p
    d = diskmod.scan(root)
    r = rb.snapshot(base)
    return planner.build_plan(root, d, r, load_state(root), force=force, base=base)


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


def apply(root, plan, base="", selections=None, recheck=True):
    """Esegue il piano: prima il disco, poi Resolve (in una sola chiamata).

    recheck: rifà l'analisi e si ferma se nel frattempo è cambiato qualcosa.
    Ritorna [(azione, esito)]."""
    root = os.path.abspath(root)
    if plan.blocked:
        raise RuntimeError(plan.blocked)
    with Lock(root):
        if recheck:
            fresh = analyze(root, base=base, force=plan.forced)
            if fresh.signature() != plan.signature():
                raise PlanChanged(fresh)
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
        if pairs:
            results, snap = rb.apply([b for _, b in pairs], base=base)
            report += [(a, out) for (a, _), out in zip(pairs, results)]
        else:
            snap = rb.snapshot(base)
        save_state(root, current_state(root, base, snap, load_state(root) if not plan.forced else {}))
        return report


def current_state(root, base, snap, prev):
    """La foto concordata: ciò che esiste da entrambe le parti, più i bin che l'utente ha
    deciso di tenere anche se la loro cartella è sparita (così non la ricreo)."""
    d = diskmod.scan(root)
    root_real = os.path.realpath(root)
    folders = {p: {"uid": snap.bins[p], "ino": ino} for p, ino in d.folders.items() if p in snap.bins}
    uid_to_path = {u: p for p, u in snap.bins.items() if u}
    for p, rec in prev.get("folders", {}).items():
        now = uid_to_path.get(rec.get("uid")) if rec.get("uid") else (p if p in snap.bins else None)
        if now and now not in folders and now not in d.folders:
            folders[now] = {"uid": snap.bins[now], "ino": None, "missing": True}
    links = {}
    for c in snap.clips:
        if not c["file"]:
            continue
        real = os.path.realpath(c["file"])
        if not real.startswith(root_real + os.sep):
            continue
        rel = os.path.relpath(real, root_real).replace(os.sep, "/")
        if rel in d.files:
            links[c["uid"]] = {"ino": d.files[rel]["ino"], "fp": d.files[rel]["fp"],
                               "bin": c["bin"], "path": rel}
    return {"version": 2, "project": snap.project, "base": base, "folders": folders,
            "links": links}
