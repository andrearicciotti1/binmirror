"""Lancia resolve_helper.py con il primo Python che riesce a parlare con Resolve.

Stesso schema di DropCut: prima il Python portatile incluso nell'app
(Contents/Frameworks/pyruntime), poi quelli di sistema. BINMIRROR_PYTHON vince
su tutto (utile nei test).
"""
import json
import logging
import os
import shutil
import subprocess
import sys

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolve_helper.py")
log = logging.getLogger("binmirror")


class ResolveError(Exception):
    pass


def _bundled_python():
    if not getattr(sys, "frozen", False):
        return None
    roots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    for root in filter(None, roots):
        for name in ("pyruntime/bin/python3.12", "pyruntime/bin/python3", "pyruntime/python.exe"):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                return p
    return None


def candidate_pythons():
    cands = []
    if os.environ.get("BINMIRROR_PYTHON"):
        cands.append(os.environ["BINMIRROR_PYTHON"])
    bp = _bundled_python()
    if bp:
        cands.append(bp)
    for p in ("/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if os.path.isfile(p) and p not in cands:
            cands.append(p)
    w = shutil.which("python3") or shutil.which("python")
    if w and w not in cands:
        cands.append(w)
    return cands


def call(op, timeout=120, **kwargs):
    payload = json.dumps(dict(op=op, **kwargs))
    last = "nessun Python trovato"
    for py in candidate_pythons():
        try:
            proc = subprocess.run([py, HELPER], input=payload, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if op == "apply":
                # Mai ripetere un apply con un altro Python: rifarebbe le stesse modifiche.
                raise ResolveError("Resolve non ha finito in tempo: una parte delle modifiche "
                                   "potrebbe essere già fatta. Rianalizza prima di riapplicare.")
            last = "Resolve non risponde (timeout)"
            continue
        except OSError as e:
            last = str(e)
            continue
        err = proc.stderr or ""
        if "xcodebuild -license" in err or "agreed to the Xcode" in err:
            last = "Licenza Xcode da accettare: sudo xcodebuild -license"
            continue
        if err:
            log.info("helper stderr (%s): %s", py, err[:800])
        lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        try:
            res = json.loads(lines[-1])
        except (IndexError, ValueError):
            last = "risposta non valida dall'helper (%s)" % py
            if op == "apply" and "BINMIRROR_STARTED" in (proc.stdout or ""):
                raise ResolveError(last + ": rianalizza prima di riapplicare.")
            continue
        if not res.get("ok"):
            raise ResolveError(res.get("err", "errore sconosciuto"))
        return res
    raise ResolveError(last)


class ResolveSnapshot:
    """bins: path -> uid (uid None se la versione di Resolve non lo espone)
    clips: lista di {"uid", "bin", "file", "name"}
    skipped: bin che non si possono rispecchiare ({"path", "why": "slash"|"dup"})"""

    def __init__(self, data):
        self.project = data.get("project", "")
        self.bins = {b["path"]: b["uid"] for b in data["bins"]}
        self.clips = data["clips"]
        self.skipped = data.get("skipped", [])
        self.has_folder_uid = bool(data.get("folder_uid")) and all(self.bins.values())
        self.uid_to_path = {u: p for p, u in self.bins.items() if u}


def snapshot(base=""):
    return ResolveSnapshot(call("snapshot", base=base))


def apply(actions, base="", date_field=""):
    """Esegue le azioni e ritorna (esiti, foto del Media Pool dopo le modifiche).
    date_field: campo metadati dove scrivere la data di import ("" = nessuno)."""
    res = call("apply", base=base, actions=actions, date_field=date_field, timeout=900)
    return res["results"], ResolveSnapshot(res["snapshot"])
