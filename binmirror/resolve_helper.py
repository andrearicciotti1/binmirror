"""Helper che parla con DaVinci Resolve via fusionscript.

Gira in un processo Python separato (come in DropCut): richiesta JSON su stdin,
risposta JSON sull'ultima riga di stdout. Così un crash o un blocco di fusionscript
non si porta dietro l'app. Deve restare compatibile con Python 3.6+ e senza
dipendenze: viene eseguito dal Python portatile incluso o da quello di sistema.

Operazioni:
  {"op": "ping"}
  {"op": "snapshot", "base": "MATERIALI"}           base = bin radice ("" = Master)
  {"op": "apply", "base": "...", "actions": [...],   vedi APPLY qui sotto; risponde anche
   "date_field": "Comments"}                          con la foto del Media Pool dopo le modifiche
"""
import glob
import importlib.machinery
import importlib.util
import json
import os
import re
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


def _file_path(item):
    try:
        fp = item.GetClipProperty("File Path") or ""
    except Exception:
        fp = ""
    if isinstance(fp, dict):
        fp = fp.get("File Path", "")
    return fp


def _walk(base_folder):
    """Ritorna (bins, clips, skipped) con percorsi relativi alla base.

    I bin con "/" nel nome o con un fratello omonimo non si possono rispecchiare su disco:
    finiscono in skipped e non si scende dentro."""
    bins, clips, skipped = [], [], []

    def rec(folder, rel):
        for it in folder.GetClipList() or []:
            clips.append({"uid": it.GetUniqueId(), "bin": rel, "file": _file_path(it),
                          "name": it.GetName()})
        seen = set()
        for sub in folder.GetSubFolderList() or []:
            name = sub.GetName()
            p = _join(rel, name)
            if "/" in name:
                skipped.append({"path": p, "why": "slash"})
                continue
            if name.lower() in seen:
                skipped.append({"path": p, "why": "dup"})
                continue
            seen.add(name.lower())
            bins.append({"path": p, "uid": _folder_uid(sub)})
            rec(sub, p)

    rec(base_folder, "")
    return bins, clips, skipped


def _snapshot(mp, base, proj):
    base_folder = _folder_at(mp, base, create=bool(base))
    bins, clips, skipped = _walk(base_folder)
    return {"project": proj.GetName(), "bins": bins, "clips": clips, "skipped": skipped,
            "folder_uid": _folder_uid(base_folder) is not None}


class Pool:
    """Accesso al Media Pool con indici: si cammina l'albero una volta sola,
    non una volta per ogni azione."""

    def __init__(self, mp, base, date_field=""):
        self.mp, self.base = mp, base
        self.date_field = date_field
        self.stamp = time.strftime("%Y-%m-%d %H:%M")
        self._clips = None
        self._bins = None

    def full(self, rel):
        return _join(self.base, rel) if rel else self.base

    def ensure(self, rel):
        f = _folder_at(self.mp, self.full(rel), create=True)
        return f

    def bins_changed(self):
        self._bins = None

    def _index(self):
        clips, bins = {}, {}

        def rec(folder, path):
            for it in folder.GetClipList() or []:
                clips[it.GetUniqueId()] = it
            for sub in folder.GetSubFolderList() or []:
                p = _join(path, sub.GetName())
                bins[_folder_uid(sub) or ("path:" + p)] = (sub, p)
                rec(sub, p)
        rec(_folder_at(self.mp, self.base), "")
        self._clips, self._bins = clips, bins

    def find_bin(self, uid=None, rel=None):
        if self._bins is None:
            self._index()
        key = uid or ("path:" + rel if rel is not None else None)
        hit = self._bins.get(key)
        if hit:
            return hit
        if rel is not None:
            for f, p in self._bins.values():
                if p == rel:
                    return f, p
        return None, None

    def find_clip(self, uid):
        if self._clips is None:
            self._index()
        return self._clips.get(uid)


# ── APPLY ─────────────────────────────────────────────────────────────────────
# Le azioni arrivano già ordinate. move_clip, import e relink consecutivi con la
# stessa destinazione diventano UNA sola chiamata a Resolve (MoveClips/ImportMedia/
# RelinkClips accettano liste): con centinaia di file è la differenza tra secondi e minuti.

GROUPED = {"move_clip", "import", "import_seq", "relink", "delete_clips"}
_SEQ_RANGE = re.compile(r"\[(\d+)-(\d+)\]")


def _stamp(pool, items):
    """Data di import nel campo scelto, solo se è vuoto: i dati del montatore non si toccano."""
    field = pool.date_field
    if not field:
        return
    for it in items:
        try:
            cur = it.GetMetadata(field)
            if isinstance(cur, dict):
                cur = cur.get(field, "")
            if cur:
                continue
            if field == "Keywords":
                it.SetMetadata(field, "BinMirror " + pool.stamp)
            else:
                it.SetMetadata(field, pool.stamp)
        except Exception:
            pass


def _seq_end(item):
    m = _SEQ_RANGE.search(_file_path(item))
    return int(m.group(2)) if m else None


def _update_seq(pool, a):
    """Sequenza cresciuta (render in corso): la clip deve prendere i fotogrammi nuovi.
    Provato su Resolve Studio 21.1.1: RelinkClips sulla cartella non rilegge il disco
    (la clip resta [0001-0012]); ReplaceClip col percorso del primo fotogramma sì: Resolve
    riconosce tutta la sequenza ([0001-0020]) e la clip resta la stessa (stesso id).
    ReplaceClip però svuota i metadati (Comments, Description, Keywords…; nome, colore e
    marker restano): li salvo prima e li rimetto dopo."""
    it = pool.find_clip(a["uid"])
    if it is None:
        return "clip non trovata"
    if (_seq_end(it) or 0) >= a["end"]:
        return "ok"
    try:
        meta = it.GetMetadata() or {}
    except Exception:
        meta = {}
    it.ReplaceClip(a["file"])
    if isinstance(meta, dict) and meta:
        try:
            it.SetMetadata(meta)
        except Exception:
            pass
    end = _seq_end(it)
    if end is not None and end >= a["end"]:
        return "ok"
    if end is None:
        return "non aggiornata: Resolve l'ha letta come fermo immagine (reimportala a mano)"
    return "non aggiornata: Resolve non ha preso i fotogrammi nuovi (reimportala a mano)"


def _import_timeline(pool, a):
    mp = pool.mp
    mp.SetCurrentFolder(pool.ensure(a.get("bin", "")))
    path = a["file"]
    name = os.path.splitext(os.path.basename(path.rstrip("/")))[0]
    if os.path.isdir(path):         # pacchetto .fcpxml di Resolve 21: il file vero è dentro
        path = os.path.join(path, "Info.fcpxml")
    # Le clip della timeline sono già nel Media Pool (le ho appena sincronizzate):
    # niente reimport delle sorgenti, sennò si creano doppioni.
    tl = mp.ImportTimelineFromFile(path, {"timelineName": name, "importSourceClips": False})
    return "ok" if tl else "ImportTimelineFromFile fallito"


def _group_key(a):
    if a["kind"] == "relink":
        return os.path.dirname(a["file"])
    return a.get("bin", "")


def _apply_bin_op(pool, a):
    mp, k = pool.mp, a["kind"]
    if k == "update_seq":
        return _update_seq(pool, a)
    if k == "import_timeline":
        return _import_timeline(pool, a)
    if k == "create_bin":
        pool.ensure(a["path"])
        pool.bins_changed()
        return "ok"
    if k == "move_bin":
        f, cur = pool.find_bin(a.get("uid"), a.get("from"))
        if f is None:
            return "bin non trovato"
        to = a["to"]
        if cur == to:
            return "già fatto"
        parent_rel, _, name = to.rpartition("/")
        pool.bins_changed()
        if f.GetName() == name:
            ok = mp.MoveFolders([f], pool.ensure(parent_rel))
            return "ok" if ok else "MoveFolders fallito"
        # Rinomina: le API non hanno SetName per i bin. Bin nuovo, ci sposto dentro
        # tutto, poi tolgo il vecchio se è rimasto vuoto.
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
        pool.bins_changed()
        return "ok" if mp.DeleteFolders([f]) else "DeleteFolders fallito"
    return "azione sconosciuta: " + k


def _apply_group(pool, kind, key, acts):
    mp = pool.mp
    if kind == "import":
        mp.SetCurrentFolder(pool.ensure(key))
        files = [a["file"] for a in acts]
        items = mp.ImportMedia(files) or []
        _stamp(pool, items)
        got = {os.path.realpath(_file_path(it)) for it in items}
        if len(items) == len(files):
            return ["ok"] * len(acts)
        return ["ok" if os.path.realpath(f) in got else "ImportMedia non l'ha importato"
                for f in files]
    if kind == "import_seq":
        mp.SetCurrentFolder(pool.ensure(key))
        infos = [{"FilePath": os.path.join(os.path.dirname(a["file"]),
                                           "%s%%0%dd%s" % (a["prefix"], a["pad"], a["ext"])),
                  "StartIndex": a["start"], "EndIndex": a["end"]} for a in acts]
        items = mp.ImportMedia(infos) or []
        _stamp(pool, items)
        return ["ok"] * len(acts) if len(items) == len(acts) else \
            ["ImportMedia: %d sequenze su %d" % (len(items), len(acts))] * len(acts)
    items = [pool.find_clip(a["uid"]) for a in acts]
    found = [it for it in items if it is not None]
    out = ["clip non trovata" if it is None else None for it in items]
    if not found:
        return out
    if kind == "move_clip":
        ok = mp.MoveClips(found, pool.ensure(key))
        res = "ok" if ok else "MoveClips fallito"
    elif kind == "delete_clips":
        res = "ok" if mp.DeleteClips(found) else "DeleteClips fallito"
    else:  # relink
        if mp.RelinkClips(found, key):
            res = "ok"
        else:
            # Uno per uno, così si sa quale non va; ReplaceClip come riserva.
            for i, (it, a) in enumerate(zip(items, acts)):
                if it is None:
                    continue
                if mp.RelinkClips([it], key):
                    out[i] = "ok"
                    continue
                try:
                    out[i] = "ok (ReplaceClip)" if it.ReplaceClip(a["file"]) else "relink fallito"
                except Exception:
                    out[i] = "relink fallito"
            return out
    return [r if r is not None else res for r in out]


def apply_all(pool, actions):
    results = [None] * len(actions)
    i = 0
    while i < len(actions):
        a = actions[i]
        if a["kind"] in GROUPED:
            key = _group_key(a)
            j = i + 1
            while j < len(actions) and actions[j]["kind"] == a["kind"] and _group_key(actions[j]) == key:
                j += 1
            try:
                res = _apply_group(pool, a["kind"], key, actions[i:j])
            except Exception as e:
                res = ["errore: %s" % e] * (j - i)
            results[i:j] = res
            i = j
        else:
            try:
                results[i] = _apply_bin_op(pool, a)
            except Exception as e:
                results[i] = "errore: %s" % e
            i += 1
    return results


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

    if req.get("op") == "snapshot":
        return _out(dict(ok=True, **_snapshot(mp, base, proj)))
    if req.get("op") == "apply":
        sys.stdout.write("BINMIRROR_STARTED\n")
        sys.stdout.flush()
        results = apply_all(Pool(mp, base, req.get("date_field", "")), req.get("actions", []))
        return _out({"ok": True, "results": results, "snapshot": _snapshot(mp, base, proj)})
    return _out({"ok": False, "err": "op sconosciuta"})


if __name__ == "__main__":
    main()
