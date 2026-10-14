"""Helper che parla con DaVinci Resolve via fusionscript.

Gira in un processo Python separato (come in DropCut): richiesta JSON su stdin,
risposta JSON sull'ultima riga di stdout. Così un crash o un blocco di fusionscript
non si porta dietro l'app. Deve restare compatibile con Python 3.6+ e senza
dipendenze: viene eseguito dal Python portatile incluso o da quello di sistema.

Operazioni:
  {"op": "ping"}
  {"op": "snapshot", "base": "MATERIALI"}           base = bin radice ("" = Master)
  {"op": "apply", "base": "...", "actions": [...]}   vedi APPLY qui sotto
"""
import glob
import importlib.machinery
import importlib.util
import json
import os
import sys
import time

MAC_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"


def _find_lib():
    if sys.platform == "darwin":
        cands = ["/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
                 "/Applications/DaVinci Resolve Studio/DaVinci Resolve Studio.app/Contents/Libraries/Fusion/fusionscript.so"]
        for app in glob.glob("/Applications/*Resolve*.app") + glob.glob("/Applications/*Resolve*/*Resolve*.app"):
            cands.append(os.path.join(app, "Contents/Libraries/Fusion/fusionscript.so"))
    elif sys.platform.startswith("win"):
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        cands = [os.path.join(pf, "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll")]
        cands += glob.glob(os.path.join(pf, "Blackmagic Design", "*Resolve*", "fusionscript.dll"))
    else:
        cands = ["/opt/resolve/libs/Fusion/fusionscript.so"]
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


def _out(obj):
    sys.stdout.write("\n" + json.dumps(obj) + "\n")
    sys.stdout.flush()


def _load_dvr():
    # Niente wrapper DaVinciResolveScript.py: torna None anche quando funziona (vedi skill).
    sys.path.append(MAC_MODULES)
    lib = os.environ.setdefault("RESOLVE_SCRIPT_LIB", _find_lib())
    if lib.endswith(".dll"):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(os.path.dirname(lib))
        loader = importlib.machinery.ExtensionFileLoader("fusionscript", lib)
        spec = importlib.util.spec_from_file_location("fusionscript", lib, loader=loader)
    else:
        spec = importlib.util.spec_from_file_location("fusionscript", lib)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _connect(dvr):
    for _ in range(4):
        for host in (None, "127.0.0.1", "localhost", "::1"):
            try:
                r = dvr.scriptapp("Resolve", host) if host else dvr.scriptapp("Resolve")
            except Exception:
                r = None
            if r:
                return r
        time.sleep(0.7)
    return None


# ── navigazione Media Pool ────────────────────────────────────────────────────

def _split(path):
    return [p for p in (path or "").split("/") if p]


def _child(folder, name):
    for f in folder.GetSubFolderList() or []:
        if f.GetName() == name:
            return f
    return None


def _folder_at(mp, path, create=False):
    f = mp.GetRootFolder()
    for name in _split(path):
        nxt = _child(f, name)
        if nxt is None:
            if not create:
                return None
            nxt = mp.AddSubFolder(f, name)
            if not nxt:
                raise RuntimeError("AddSubFolder fallito: " + name)
        f = nxt
    return f


def _folder_uid(folder):
    try:
        u = folder.GetUniqueId()
        return u or None
    except Exception:
        return None


def _join(a, b):
    return (a + "/" + b) if a else b


def _walk(base_folder):
    """Ritorna (bins, clips) con percorsi relativi alla base."""
    bins, clips = [], []

    def rec(folder, rel):
        for it in folder.GetClipList() or []:
            try:
                fp = it.GetClipProperty("File Path") or ""
            except Exception:
                fp = ""
            if isinstance(fp, dict):
                fp = fp.get("File Path", "")
            clips.append({"uid": it.GetUniqueId(), "bin": rel, "file": fp, "name": it.GetName()})
        for sub in folder.GetSubFolderList() or []:
            p = _join(rel, sub.GetName())
            bins.append({"path": p, "uid": _folder_uid(sub)})
            rec(sub, p)

    rec(base_folder, "")
    return bins, clips


class Pool:
    def __init__(self, mp, base):
        self.mp, self.base = mp, base

    def full(self, rel):
        return _join(self.base, rel) if rel else self.base

    def ensure(self, rel):
        return _folder_at(self.mp, self.full(rel), create=True)

    def find_bin(self, uid=None, rel=None):
        base = _folder_at(self.mp, self.base)
        if uid:
            def rec(folder, path):
                for sub in folder.GetSubFolderList() or []:
                    p = _join(path, sub.GetName())
                    if _folder_uid(sub) == uid:
                        return sub, p
                    hit = rec(sub, p)
                    if hit:
                        return hit
                return None
            hit = rec(base, "")
            if hit:
                return hit
        if rel is not None:
            f = _folder_at(self.mp, self.full(rel))
            if f:
                return f, rel
        return None, None

    def find_clip(self, uid):
        def rec(folder):
            for it in folder.GetClipList() or []:
                if it.GetUniqueId() == uid:
                    return it
            for sub in folder.GetSubFolderList() or []:
                hit = rec(sub)
                if hit:
                    return hit
            return None
        return rec(_folder_at(self.mp, self.base))


# ── APPLY ─────────────────────────────────────────────────────────────────────

def _apply_one(pool, a):
    mp, k = pool.mp, a["kind"]
    if k == "create_bin":
        pool.ensure(a["path"])
        return "ok"
    if k == "move_bin":
        f, cur = pool.find_bin(a.get("uid"), a.get("from"))
        if f is None:
            return "bin non trovato"
        to = a["to"]
        if cur == to:
            return "già fatto"
        parent_rel, _, name = to.rpartition("/")
        if f.GetName() == name:
            ok = mp.MoveFolders([f], pool.ensure(parent_rel))
            return "ok" if ok else "MoveFolders fallito"
        # Rinomina: nuovo bin, ci sposto dentro tutto, poi tolgo il vecchio se è vuoto.
        new = pool.ensure(to)
        clips = f.GetClipList() or []
        if clips and not mp.MoveClips(clips, new):
            return "MoveClips fallito"
        subs = f.GetSubFolderList() or []
        if subs and not mp.MoveFolders(subs, new):
            return "MoveFolders fallito"
        if not (f.GetClipList() or []) and not (f.GetSubFolderList() or []):
            mp.DeleteFolders([f])
        return "ok (rinominato)"
    if k == "delete_bin":
        f, _ = pool.find_bin(a.get("uid"), a.get("path"))
        if f is None:
            return "già sparito"
        if (f.GetClipList() or []) or (f.GetSubFolderList() or []):
            return "lasciato: non è vuoto"
        return "ok" if mp.DeleteFolders([f]) else "DeleteFolders fallito"
    if k == "move_clip":
        it = pool.find_clip(a["uid"])
        if it is None:
            return "clip non trovata"
        return "ok" if mp.MoveClips([it], pool.ensure(a["bin"])) else "MoveClips fallito"
    if k == "relink":
        it = pool.find_clip(a["uid"])
        if it is None:
            return "clip non trovata"
        if mp.RelinkClips([it], os.path.dirname(a["file"])):
            return "ok"
        try:
            if it.ReplaceClip(a["file"]):
                return "ok (ReplaceClip)"
        except Exception:
            pass
        return "relink fallito"
    if k == "import":
        mp.SetCurrentFolder(pool.ensure(a["bin"]))
        items = mp.ImportMedia([a["file"]])
        return "ok" if items else "ImportMedia ha restituito 0 clip"
    return "azione sconosciuta: " + k


def main():
    req = json.loads(sys.stdin.read() or "{}")
    try:
        dvr = _load_dvr()
    except Exception as e:
        return _out({"ok": False, "err": "fusionscript non caricabile: %s" % e})
    R = _connect(dvr)
    if not R:
        return _out({"ok": False, "err": "Resolve non raggiungibile",
                     "lib": os.environ.get("RESOLVE_SCRIPT_LIB")})
    if req.get("op") == "ping":
        return _out({"ok": True})
    proj = R.GetProjectManager().GetCurrentProject()
    if not proj:
        return _out({"ok": False, "err": "Nessun progetto aperto"})
    mp = proj.GetMediaPool()
    base = req.get("base", "")
    base_folder = _folder_at(mp, base, create=bool(base))
    pool = Pool(mp, base)

    if req.get("op") == "snapshot":
        bins, clips = _walk(base_folder)
        return _out({"ok": True, "project": proj.GetName(), "bins": bins, "clips": clips,
                     "folder_uid": _folder_uid(base_folder) is not None})
    if req.get("op") == "apply":
        results = []
        for a in req.get("actions", []):
            try:
                results.append(_apply_one(pool, a))
            except Exception as e:
                results.append("errore: %s" % e)
        return _out({"ok": True, "results": results})
    return _out({"ok": False, "err": "op sconosciuta"})


if __name__ == "__main__":
    main()
