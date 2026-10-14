"""Mette insieme disco, Resolve e piano: analizza() e applica()."""
import json
import os

from . import disk as diskmod
from . import planner
from . import resolve_bridge as rb

DISK_ORDER = {"mkdir": 0, "move": 1, "trash": 2}
RES_ORDER = {"create_bin": 0, "move_bin": 1, "move_clip": 2, "relink": 3, "import": 4, "delete_bin": 5}


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


def analyze(root, base="", force=False):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        p = planner.Plan()
        p.blocked = "La cartella radice non c'è: disco scollegato? (%s)" % root
        return p
    d = diskmod.scan(root)
    r = rb.snapshot(base)
    return planner.build_plan(root, d, r, load_state(root), force=force)


def _sorted(actions, order, folder_first=False):
    def key(a):
        p = a.get("path") or a.get("src") or a.get("to") or a.get("file") or ""
        depth = p.count("/")
        if a["kind"] == "delete_bin":
            depth = -depth
        # nelle mosse su disco, prima le cartelle e poi i file
        is_file = 1 if (a["kind"] == "move" and not a.get("folder")) else 0
        return (order[a["kind"]], is_file, depth)
    return sorted(actions, key=key)


def apply(root, plan, base=""):
    """Esegue il piano: prima il disco, poi Resolve. Ritorna [(azione, esito)]."""
    root = os.path.abspath(root)
    if plan.blocked:
        raise RuntimeError(plan.blocked)
    ops = diskmod.DiskOps(root)
    report = []
    actual = {}   # percorso previsto → percorso reale (se c'era già un file con quel nome)
    for a in _sorted(plan.disk, DISK_ORDER):
        try:
            if a["kind"] == "mkdir":
                out = ops.mkdir(a["path"])
            elif a["kind"] == "move":
                out = ops.move(a["src"], a["dst"])
                if out not in ("già fatto", "sorgente sparita") and out != a["dst"]:
                    actual[a["dst"]] = out
            else:
                out = ops.trash(a["path"])
        except OSError as e:
            out = "errore: %s" % e
        report.append((a, out))

    res_actions = []
    for a in _sorted(plan.resolve, RES_ORDER):
        b = {k: v for k, v in a.items() if k != "why"}
        if "file" in b:
            b["file"] = diskmod.abs_path(root, actual.get(b["file"], b["file"]))
        res_actions.append((a, b))
    if res_actions:
        results = rb.apply([b for _, b in res_actions], base=base)
        report += [(a, out) for (a, _), out in zip(res_actions, results)]

    save_state(root, current_state(root, base))
    return report


def current_state(root, base=""):
    """La foto concordata: solo ciò che esiste da entrambe le parti."""
    d = diskmod.scan(root)
    r = rb.snapshot(base)
    root_real = os.path.realpath(root)
    folders = {p: {"uid": r.bins[p], "ino": ino} for p, ino in d.folders.items() if p in r.bins}
    links = {}
    for c in r.clips:
        if not c["file"]:
            continue
        real = os.path.realpath(c["file"])
        if not real.startswith(root_real + os.sep):
            continue
        rel = os.path.relpath(real, root_real).replace(os.sep, "/")
        if rel in d.files:
            links[c["uid"]] = {"ino": d.files[rel]["ino"], "fp": d.files[rel]["fp"],
                               "bin": c["bin"], "path": rel}
    return {"version": 1, "project": r.project, "base": base, "folders": folders, "links": links}
