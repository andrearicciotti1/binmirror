"""Il cervello: confronta disco, Resolve e l'ultimo stato concordato e decide cosa fare.

Confronto a tre vie. Lo "stato" è la foto di com'erano disco e Media Pool
all'ultima sincronizzazione: senza, non si potrebbe capire chi ha cambiato cosa.

  - cartella/bin nuovi da una parte  → creati dall'altra
  - bin spostato o rinominato in Resolve → stessa cosa alla cartella
  - cartella spostata o rinominata su disco → stessa cosa al bin
  - clip spostata tra bin in Resolve → il file si sposta nella cartella giusta (+ relink)
  - file spostato su disco → la clip va nel bin giusto (+ relink)
  - file nuovo su disco → importato nel bin giusto
  - clip tolta da Resolve → il file va in _CESTINO
  - bin tolto da Resolve → la cartella va in _CESTINO
  - cartella sparita dal disco → il bin si toglie solo se è vuoto

Prima sincronizzazione (nessuno stato): niente va nel cestino, si unisce soltanto.
Se una clip non è nel bin che corrisponde alla cartella del suo file, comanda il disco.
"""
import os
import posixpath

MAX_TRASH_RATIO = 0.25   # oltre questa quota di file nel cestino, si blocca (disco scollegato?)
MAX_TRASH_FREE = 5


class Plan:
    def __init__(self):
        self.disk = []       # azioni sul disco
        self.resolve = []    # azioni sul Media Pool
        self.notes = []      # cose che l'utente deve sapere ma che non tocco
        self.blocked = None  # motivo per non applicare niente
        self.outside = 0     # clip con file fuori dalla cartella radice

    def is_empty(self):
        return not self.disk and not self.resolve

    def add(self, side, kind, why, **args):
        a = dict(kind=kind, why=why, **args)
        (self.disk if side == "disk" else self.resolve).append(a)
        return a


def _depth(p):
    return p.count("/") + (1 if p else 0)


def _dirname(p):
    return posixpath.dirname(p)


def _join(d, name):
    return (d + "/" + name) if d else name


def _under(p, top):
    return p == top or p.startswith(top + "/")


def _remap(path, moves):
    best = None
    for old, new in moves:
        if _under(path, old) and (best is None or len(old) > len(best[0])):
            best = (old, new)
    return path if best is None else best[1] + path[len(best[0]):]


def _implied(old, new, moves):
    """True se lo spostamento old→new è già coperto da quello di una cartella madre."""
    for o, n in moves:
        if o != old and old.startswith(o + "/") and new == n + old[len(o):]:
            return True
    return False


def _label(p):
    return p if p else "(radice)"


def build_plan(root, disk, res, state, force=False):
    plan = Plan()
    S_f = state.get("folders", {})
    links = state.get("links", {})
    root_real = os.path.realpath(root)

    if not os.path.isdir(root):
        plan.blocked = "La cartella radice non c'è: disco scollegato? (%s)" % root
        return plan
    if links and not disk.files:
        plan.blocked = ("Nella cartella radice non trovo più nessun file, ma l'ultima volta ce "
                        "n'erano %d. Il disco è collegato?" % len(links))
        return plan

    # ── 1. cartelle ↔ bin ────────────────────────────────────────────────────
    disk_by_ino = {ino: p for p, ino in disk.folders.items()}
    claimed_disk, claimed_bins = set(), set()
    disk_moves, res_moves, trash_dirs = [], [], []
    bin_deletes = []

    def link_fps(folder):
        return {l["fp"] for l in links.values() if _dirname(l["path"]) == folder}

    def match_by_content(old):
        """Per i dischi exFAT, dove l'id della cartella cambia: stessa roba dentro = stessa cartella."""
        want = link_fps(old)
        if not want:
            return None
        best, score = None, 0.0
        for p in disk.folders:
            if p in S_f or p in claimed_disk:
                continue
            have = {r["fp"] for r in disk.files_in(p).values()}
            s = len(want & have) / float(len(want))
            if s > score:
                best, score = p, s
        return best if score >= 0.8 else None

    for path, rec in sorted(S_f.items(), key=lambda kv: _depth(kv[0])):
        uid = rec.get("uid")
        if res.has_folder_uid and uid:
            vpath = res.uid_to_path.get(uid)
        else:
            vpath = path if path in res.bins else None
        if disk.folders.get(path) == rec.get("ino"):
            dpath = path
        elif rec.get("ino") in disk_by_ino:
            dpath = disk_by_ino[rec["ino"]]
        elif path in disk.folders:
            dpath = path
        else:
            dpath = match_by_content(path)
        if dpath:
            claimed_disk.add(dpath)
        if vpath:
            claimed_bins.add(vpath)

        if vpath == path and dpath == path:
            continue
        if vpath and dpath:
            if dpath == path:                       # cambiato in Resolve
                disk_moves.append((path, vpath))
            elif vpath == path:                     # cambiato su disco
                res_moves.append((path, dpath, uid))
            elif vpath != dpath:                    # cambiato da entrambe le parti
                plan.notes.append("Conflitto: '%s' è diventato '%s' in Resolve e '%s' su disco. "
                                  "Vince il disco." % (path, vpath, dpath))
                res_moves.append((vpath, dpath, uid))
        elif vpath and not dpath:                   # cartella sparita dal disco
            busy = any(_under(c["bin"], vpath) for c in res.clips) or \
                any(_under(b, vpath) and b != vpath for b in res.bins)
            if busy:
                plan.notes.append("La cartella '%s' non c'è più su disco, ma il bin ha ancora "
                                  "delle clip: lo lascio." % path)
            else:
                bin_deletes.append((vpath, uid))
        elif dpath and not vpath:                   # bin tolto da Resolve
            if res.has_folder_uid:
                trash_dirs.append(dpath)
            else:
                plan.notes.append("Il bin '%s' non c'è più in Resolve, ma questa versione di "
                                  "Resolve non mi dà gli id dei bin: non tocco la cartella." % path)

    for old, new in disk_moves:
        if not _implied(old, new, disk_moves):
            plan.add("disk", "move", "Bin '%s' spostato/rinominato in Resolve → '%s'" % (old, new),
                     src=old, dst=new, folder=True)
    pairs = [(o, n) for o, n, _ in res_moves]
    for old, new, uid in res_moves:
        if not _implied(old, new, pairs):
            plan.add("resolve", "move_bin", "Cartella '%s' spostata/rinominata su disco → '%s'"
                     % (old, new), uid=uid, **{"from": old, "to": new})
    trash_dirs = [t for t in trash_dirs if not any(o != t and _under(t, o) for o in trash_dirs)]
    for t in trash_dirs:
        plan.add("disk", "trash", "Bin '%s' tolto da Resolve → cartella in _CESTINO" % t, path=t)
    for p, uid in sorted(bin_deletes, key=lambda x: -_depth(x[0])):
        plan.add("resolve", "delete_bin", "Cartella '%s' sparita dal disco → tolgo il bin vuoto" % p,
                 uid=uid, path=p)
    res_pairs = pairs

    def under_trash(p):
        return any(_under(p, t) for t in trash_dirs)

    future_bins = {_remap(b, res_pairs) for b in res.bins}
    for p in sorted(disk.folders, key=_depth):
        if p in claimed_disk or under_trash(p):
            continue
        if p not in future_bins:
            plan.add("resolve", "create_bin", "Cartella nuova su disco '%s'" % p, path=p)
    future_dirs = {_remap(f, disk_moves) for f in disk.folders if not under_trash(f)}
    for p in sorted(res.bins, key=_depth):
        if p in claimed_bins:
            continue
        if p not in future_dirs:
            plan.add("disk", "mkdir", "Bin nuovo in Resolve '%s'" % p, path=p)

    # ── 2. clip ↔ file ───────────────────────────────────────────────────────
    by_ino = {r["ino"]: p for p, r in disk.files.items()}
    by_fp = {}
    for p, r in disk.files.items():
        by_fp.setdefault(r["fp"], []).append(p)

    def locate(link):
        old = link["path"]
        if old in disk.files and disk.files[old]["fp"] == link["fp"]:
            return old
        p = by_ino.get(link["ino"])
        if p and (disk.files[p]["fp"] == link["fp"] or posixpath.basename(p) == posixpath.basename(old)):
            return p
        cands = by_fp.get(link["fp"], [])
        same = [c for c in cands if posixpath.basename(c) == posixpath.basename(old)]
        if len(same) == 1:
            return same[0]
        if len(cands) == 1:
            return cands[0]
        return old if old in disk.files else None

    def rel_of(f):
        if not f:
            return None
        real = os.path.realpath(f)
        if not real.startswith(root_real + os.sep):
            return None
        return os.path.relpath(real, root_real).replace(os.sep, "/")

    used, final_of = set(), {}
    offline = []
    for c in res.clips:
        rel = rel_of(c["file"])
        if rel is None:
            if c["file"]:
                plan.outside += 1
            continue
        link = links.get(c["uid"])
        dpath = locate(link) if link else (rel if rel in disk.files else None)
        if dpath is None:
            offline.append(c["name"])
            continue
        if under_trash(dpath):
            continue
        name = posixpath.basename(dpath)
        if dpath in used:                                   # stesso file in due bin
            final = final_of[dpath]
            if rel != final:
                plan.add("resolve", "relink", "Ricollego '%s'" % c["name"], uid=c["uid"], file=final)
            if _remap(c["bin"], res_pairs) != _dirname(final):
                plan.notes.append("'%s' è in più bin: il file segue la prima copia, questa resta "
                                  "in '%s'." % (c["name"], _label(c["bin"])))
            continue
        used.add(dpath)
        pdir = _remap(_dirname(dpath), disk_moves)
        pbin = _remap(c["bin"], res_pairs)
        final = _join(pdir, name)
        if pdir != pbin:
            disk_moved = bool(link) and dpath != link["path"]
            bin_moved = bool(link) and c["bin"] != link["bin"]
            if bin_moved and not disk_moved:
                dst = _join(pbin, name)
                plan.add("disk", "move", "Clip '%s' spostata in Resolve nel bin '%s'"
                         % (c["name"], _label(pbin)), src=final, dst=dst)
                final = dst
            else:
                if bin_moved and disk_moved:
                    plan.notes.append("Conflitto su '%s': spostata sia in Resolve sia su disco. "
                                      "Vince il disco." % c["name"])
                why = ("File spostato su disco in '%s'" if disk_moved else
                       "Il file sta in '%s'") % _label(pdir)
                plan.add("resolve", "move_clip", "%s → sposto la clip '%s'" % (why, c["name"]),
                         uid=c["uid"], bin=pdir)
        final_of[dpath] = final
        if rel != final:
            plan.add("resolve", "relink", "Ricollego '%s' a %s" % (c["name"], final),
                     uid=c["uid"], file=final)

    linked_at = {}
    for uid, l in links.items():
        p = locate(l)
        if p:
            linked_at[p] = uid
    trash_files = []
    for p in sorted(disk.files):
        if p in used or under_trash(p):
            continue
        final = _remap(p, disk_moves)
        if p in linked_at:
            trash_files.append(final)
            plan.add("disk", "trash", "Clip tolta da Resolve → file in _CESTINO", path=final)
        else:
            plan.add("resolve", "import", "File nuovo su disco '%s'" % final,
                     file=final, bin=_dirname(final))

    if offline:
        plan.notes.append("%d clip offline (file non trovato su disco): %s"
                          % (len(offline), ", ".join(offline[:8]) + ("…" if len(offline) > 8 else "")))
    if plan.outside:
        plan.notes.append("%d clip hanno il file fuori dalla cartella radice: non le tocco."
                          % plan.outside)
    if trash_files and not force:
        limit = max(MAX_TRASH_FREE, int(len(links) * MAX_TRASH_RATIO))
        if len(trash_files) > limit:
            plan.blocked = ("Dovrei mettere %d file nel cestino: sono troppi per essere normale. "
                            "Controlla che in Resolve sia aperto il progetto giusto. "
                            "Se è tutto voluto, rilancia con --forza." % len(trash_files))
    return plan
