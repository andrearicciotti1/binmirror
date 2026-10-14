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

Quello che può far perdere lavoro non si fa da soli, si chiede (plan.choices):
  - bin tolto da Resolve con dentro dei media → cartella nel cestino o bin ripristinato?
  - cartella cancellata dal disco con clip ormai offline → tolgo bin e clip o li lascio?

Tre livelli di avviso:
  plan.blocked  ⛔ non si applica niente (disco scollegato, progetto diverso, …)
  plan.choices  ❓ decisioni con una risposta già proposta
  plan.notes    ⚠️  cose da sapere che non tocco

Prima sincronizzazione (nessuno stato): niente va nel cestino, si unisce soltanto.
Se una clip non è nel bin che corrisponde alla cartella del suo file, comanda il disco.
"""
import os
import posixpath
import re
import time

MAX_TRASH_RATIO = 0.25   # oltre questa quota di file nel cestino, si blocca
MAX_TRASH_FREE = 5
RECENT_SECONDS = 15      # un file modificato da meno di così può essere ancora in copia
_SEQ_PATH = re.compile(r"\[\d+-\d+\]")


class Plan:
    def __init__(self):
        self.disk = []        # azioni sul disco
        self.resolve = []     # azioni sul Media Pool
        self.choices = []     # decisioni per l'utente
        self.notes = []       # cose da sapere che non tocco
        self.blocked = None   # motivo per non applicare niente
        self.can_force = False  # il blocco si può superare consapevolmente
        self.forced = False
        self.outside = 0      # clip con file fuori dalla cartella radice

    def is_empty(self):
        return not self.disk and not self.resolve and not self.choices

    def add(self, side, kind, why, **args):
        a = dict(kind=kind, why=why, **args)
        (self.disk if side == "disk" else self.resolve).append(a)
        return a

    def add_choice(self, cid, question, options, default):
        """options: [{"key", "label", "disk": [...], "resolve": [...]}]"""
        self.choices.append({"id": cid, "question": question, "options": options,
                             "default": default})

    def selected_actions(self, selections=None):
        """Azioni da eseguire, comprese quelle delle risposte scelte (o proposte)."""
        disk, res = list(self.disk), list(self.resolve)
        for c in self.choices:
            key = (selections or {}).get(c["id"], c["default"])
            for o in c["options"]:
                if o["key"] == key:
                    disk += o.get("disk", [])
                    res += o.get("resolve", [])
        return disk, res

    def signature(self):
        acts = sorted((a["kind"], a["why"]) for a in self.disk + self.resolve)
        return (self.blocked, acts, sorted(c["id"] for c in self.choices))


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


def _plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def _missing_root_reason(root):
    parts = os.path.abspath(root).split(os.sep)
    if len(parts) > 2 and parts[1] == "Volumes" and not os.path.isdir(os.sep.join(parts[:3])):
        return "Il disco «%s» non è collegato." % parts[2]
    return "La cartella %s non c'è più (spostata o rinominata?)." % root


def preflight(root, res, state, base, force):
    """Controlli che bloccano tutto. Ritorna (blocked, can_force, state da usare)."""
    if not os.path.isdir(root):
        return _missing_root_reason(root), False, state
    if not os.access(root, os.W_OK):
        return "Non posso scrivere nella cartella (disco in sola lettura o permessi).", False, state
    if state:
        prev_proj, prev_base = state.get("project"), state.get("base", "")
        if (prev_proj and prev_proj != res.project) or prev_base != base:
            if not force:
                what = ("col progetto «%s»" % prev_proj) if prev_proj != res.project else \
                       ("col bin «%s»" % (prev_base or "Master"))
                return ("L'ultima volta questa cartella era sincronizzata %s, ora c'è «%s». "
                        "Se procedi la tratto come una prima sincronizzazione: unisco e basta, "
                        "senza cestinare niente."
                        % (what, res.project if prev_proj != res.project else (base or "Master")),
                        True, state)
            return None, False, {}
    return None, False, state


def build_plan(root, disk, res, state, force=False, base="", now=None):
    plan = Plan()
    plan.forced = force
    now = time.time() if now is None else now
    blocked, can_force, state = preflight(root, res, state, base, force)
    if blocked:
        plan.blocked, plan.can_force = blocked, can_force
        return plan
    S_f = state.get("folders", {})
    links = state.get("links", {})
    root_real = os.path.realpath(root)
    if links and not disk.files:
        plan.blocked = ("Nella cartella non trovo più nessun file, ma l'ultima volta ce n'erano "
                        "%d. Il disco è collegato bene? Non tocco niente." % len(links))
        return plan

    # ── 0. cose che non si possono rispecchiare ──────────────────────────────
    skipped_bins = [s["path"] for s in res.skipped]
    for s in res.skipped:
        if s["why"] == "slash":
            plan.notes.append("Il bin «%s» ha una / nel nome: su disco non può esistere. "
                              "Rinominalo per includerlo." % s["path"])
        else:
            plan.notes.append("Ci sono due bin «%s» nello stesso posto: tengo solo il primo. "
                              "Rinomina l'altro." % s["path"])
    for o in disk.opaque:
        plan.notes.append("«%s» sembra una scheda camera: non la trasformo in bin, "
                          "importala a mano se ti serve." % o)
    for d, name, n in disk.sequences:
        plan.notes.append("Sequenza di immagini «%s» (%d fotogrammi) in «%s»: importala a mano."
                          % (name, n, _label(d)))
    res_bins = {p: u for p, u in res.bins.items()
                if not any(_under(p, s) for s in skipped_bins)}

    # Maiuscole: sul Mac «MUSICA» e «Musica» sono la stessa cartella.
    fold = (lambda p: p.lower()) if disk.case_insensitive else (lambda p: p)
    disk_fold = {fold(p): p for p in disk.folders}

    # ── 1. cartelle ↔ bin ────────────────────────────────────────────────────
    disk_by_ino = {ino: p for p, ino in disk.folders.items()}
    uid_to_path = {u: p for p, u in res_bins.items() if u}
    claimed_disk, claimed_bins = set(), set()
    disk_moves, res_moves, trash_dirs = [], [], []
    gone_from_disk = []   # (bin, uid) cartella sparita, bin ancora lì
    gone_from_res = []    # cartella ancora lì, bin tolto

    fps_by_dir = {}
    for l in links.values():
        fps_by_dir.setdefault(_dirname(l["path"]), set()).add(l["fp"])

    def match_by_content(old):
        """Per i dischi exFAT, dove l'id della cartella cambia: stessa roba dentro = stessa cartella."""
        want = fps_by_dir.get(old)
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
            vpath = uid_to_path.get(uid)
        else:
            vpath = path if path in res_bins else None
        ino = rec.get("ino")
        if ino and disk.folders.get(path) == ino:
            dpath = path
        elif ino and ino in disk_by_ino:
            dpath = disk_by_ino[ino]
        elif path in disk.folders:
            dpath = path
        elif fold(path) in disk_fold:
            dpath = disk_fold[fold(path)]
        else:
            dpath = None if rec.get("missing") else match_by_content(path)
        if dpath:
            claimed_disk.add(dpath)
        if vpath:
            claimed_bins.add(vpath)

        if vpath == path and dpath == path:
            continue
        if vpath and dpath:
            if dpath == path or (fold(dpath) == fold(path) and vpath != path):
                if vpath != dpath:
                    disk_moves.append((dpath, vpath))         # cambiato in Resolve
            elif vpath == path:
                res_moves.append((path, dpath, uid))          # cambiato su disco
            elif vpath != dpath:
                plan.notes.append("Conflitto: «%s» è diventato «%s» in Resolve e «%s» su disco. "
                                  "Vince il disco." % (path, vpath, dpath))
                res_moves.append((vpath, dpath, uid))
        elif vpath and not dpath:
            if rec.get("missing"):
                continue        # già deciso: bin lasciato senza cartella
            gone_from_disk.append((vpath, uid))
        elif dpath and not vpath:
            if res.has_folder_uid:
                gone_from_res.append(dpath)
            else:
                plan.notes.append("Il bin «%s» non c'è più in Resolve, ma questa versione di "
                                  "Resolve non mi dà gli id dei bin: non tocco la cartella." % path)

    for old, new in disk_moves:
        if not _implied(old, new, disk_moves):
            plan.add("disk", "move", "Bin «%s» spostato/rinominato in Resolve → «%s»" % (old, new),
                     src=old, dst=new, folder=True)
    res_pairs = [(o, n) for o, n, _ in res_moves]
    for old, new, uid in res_moves:
        if not _implied(old, new, res_pairs):
            plan.add("resolve", "move_bin", "Cartella «%s» spostata/rinominata su disco → «%s»"
                     % (old, new), uid=uid, **{"from": old, "to": new})

    # Bin tolti da Resolve: cartella vuota → cestino; con dentro dei media → si chiede.
    gone_from_res = [t for t in gone_from_res
                     if not any(o != t and _under(t, o) for o in gone_from_res)]
    for t in gone_from_res:
        files = disk.files_under(t)
        if not files:
            plan.add("disk", "trash", "Bin «%s» tolto da Resolve → cartella (vuota) in _CESTINO" % t,
                     path=t)
            trash_dirs.append(t)
            continue
        restore = [dict(kind="create_bin", why="Ripristino il bin «%s»" % t, path=t)]
        for f in sorted(files):
            restore.append(dict(kind="import", why="Reimporto «%s»" % f, file=f, bin=_dirname(f)))
        plan.add_choice(
            "bin-tolto:" + t,
            "Hai tolto il bin «%s» da Resolve, ma la cartella su disco contiene %s."
            % (t, _plural(len(files), "file", "file")),
            [{"key": "cestino", "label": "Cartella nel _CESTINO",
              "disk": [dict(kind="trash", why="Bin «%s» tolto → cartella in _CESTINO" % t, path=t)]},
             {"key": "ripristina", "label": "Rimetti il bin in Resolve", "resolve": restore}],
            default="cestino")
        trash_dirs.append(t)    # in entrambi i casi i file non vanno trattati qui sotto

    def under_trash(p):
        return any(_under(p, t) for t in trash_dirs)

    future_bins = {fold(_remap(b, res_pairs)) for b in res_bins}
    for p in sorted(disk.folders, key=_depth):
        if p in claimed_disk or under_trash(p):
            continue
        if fold(p) not in future_bins:
            plan.add("resolve", "create_bin", "Cartella nuova su disco «%s»" % p, path=p)
    future_dirs = {fold(_remap(f, disk_moves)) for f in disk.folders if not under_trash(f)}
    for p in sorted(res_bins, key=_depth):
        if p in claimed_bins:
            continue
        if fold(p) not in future_dirs:
            plan.add("disk", "mkdir", "Bin nuovo in Resolve «%s»" % p, path=p)
        elif p not in disk.folders and fold(p) in disk_fold:
            plan.notes.append("Il bin «%s» e la cartella «%s» cambiano solo per le maiuscole: "
                              "li tratto come la stessa cosa." % (p, disk_fold[fold(p)]))

    # Sul Mac un bin «Musica» e la cartella «MUSICA» sono la stessa cosa: le clip vanno
    # nel bin che esiste già, non in un doppione con le maiuscole della cartella.
    bin_case = {fold(_remap(b, res_pairs)): _remap(b, res_pairs) for b in res_bins}

    def target_bin(d):
        return bin_case.get(fold(d), d) if disk.case_insensitive else d

    # ── 2. clip ↔ file ───────────────────────────────────────────────────────
    by_ino = {r["ino"]: p for p, r in disk.files.items()}
    by_fp = {}
    for p, r in disk.files.items():
        by_fp.setdefault(r["fp"], []).append(p)
    located = {}

    def locate(uid, link):
        if uid in located:
            return located[uid]
        old = link["path"]
        found = None
        if old in disk.files and disk.files[old]["fp"] == link["fp"]:
            found = old
        else:
            p = by_ino.get(link["ino"])
            if p and (disk.files[p]["fp"] == link["fp"]
                      or posixpath.basename(p) == posixpath.basename(old)):
                found = p
            else:
                cands = by_fp.get(link["fp"], [])
                same = [c for c in cands if posixpath.basename(c) == posixpath.basename(old)]
                if len(same) == 1:
                    found = same[0]
                elif len(cands) == 1:
                    found = cands[0]
                elif old in disk.files:
                    found = old
        located[uid] = found
        return found

    def rel_of(f):
        if not f:
            return None
        real = os.path.realpath(f)
        if not real.startswith(root_real + os.sep):
            return None
        return os.path.relpath(real, root_real).replace(os.sep, "/")

    in_bin = {}               # bin → clip che ci restano dopo il piano
    for c in res.clips:
        in_bin.setdefault(c["bin"], []).append(c)
    leaving = set()           # uid delle clip che il piano sposta in un altro bin
    used, final_of = set(), {}
    offline, unmanaged = [], 0
    for c in res.clips:
        if any(_under(c["bin"], s) for s in skipped_bins):
            continue
        if c["file"] and _SEQ_PATH.search(c["file"]):
            unmanaged += 1
            continue
        rel = rel_of(c["file"])
        if rel is None:
            if c["file"]:
                plan.outside += 1
            continue
        if any(_under(rel, o) for o in disk.opaque):
            unmanaged += 1
            continue
        link = links.get(c["uid"])
        dpath = locate(c["uid"], link) if link else (rel if rel in disk.files else None)
        if dpath is None:
            offline.append(c)
            continue
        if under_trash(dpath):
            continue
        name = posixpath.basename(dpath)
        if dpath in used:                                   # stesso file in due bin
            final = final_of[dpath]
            if rel != final:
                plan.add("resolve", "relink", "Ricollego «%s»" % c["name"], uid=c["uid"], file=final)
            if _remap(c["bin"], res_pairs) != _dirname(final):
                plan.notes.append("«%s» è in più bin: il file segue la prima copia, questa resta "
                                  "in «%s»." % (c["name"], _label(c["bin"])))
            continue
        used.add(dpath)
        pdir = _remap(_dirname(dpath), disk_moves)
        pbin = _remap(c["bin"], res_pairs)
        final = _join(pdir, name)
        if fold(pdir) != fold(pbin):
            disk_moved = bool(link) and dpath != link["path"]
            bin_moved = bool(link) and c["bin"] != link["bin"]
            if bin_moved and not disk_moved:
                dst = _join(pbin, name)
                plan.add("disk", "move", "Clip «%s» spostata in Resolve nel bin «%s»"
                         % (c["name"], _label(pbin)), src=final, dst=dst)
                final = dst
            else:
                if bin_moved and disk_moved:
                    plan.notes.append("Conflitto su «%s»: spostata sia in Resolve sia su disco. "
                                      "Vince il disco." % c["name"])
                why = ("File spostato su disco in «%s»" if disk_moved else
                       "Il file sta in «%s»") % _label(pdir)
                plan.add("resolve", "move_clip", "%s → sposto la clip «%s»" % (why, c["name"]),
                         uid=c["uid"], bin=target_bin(pdir))
                leaving.add(c["uid"])
        final_of[dpath] = final
        if rel != final:
            plan.add("resolve", "relink", "Ricollego «%s» a %s" % (c["name"], final),
                     uid=c["uid"], file=final)

    # ── 3. cartelle sparite dal disco ────────────────────────────────────────
    offline_uids = {c["uid"] for c in offline}
    gone_set = {b for b, _ in gone_from_disk}
    gone_uid = dict(gone_from_disk)
    # Si ragiona solo sulla cartella più in alto: le sottocartelle sono sparite con lei.
    tops = [(b, u) for b, u in gone_from_disk
            if not any(o != b and _under(b, o) for o in gone_set)]
    for b, uid in sorted(tops):
        if fold(b) in future_dirs:
            continue    # la cartella c'è ancora con altre maiuscole, o la ricrea uno spostamento
        sub_bins = [x for x in res_bins if x != b and _under(x, b) and x not in gone_set]
        stay = [c for x, cs in in_bin.items() if _under(x, b) for c in cs
                if c["uid"] not in leaving]
        # Prima i sotto-bin più profondi, poi il bin: Resolve toglie solo bin vuoti.
        deletes = [dict(kind="delete_bin", why="Tolgo il bin «%s»" % x, uid=gone_uid[x], path=x)
                   for x in sorted(gone_set, key=lambda x: -_depth(x)) if _under(x, b)]
        if not stay and not sub_bins:
            for d in deletes:
                d["why"] = "Cartella «%s» sparita dal disco → tolgo il bin (è vuoto)" % d["path"]
                plan.resolve.append(d)
            continue
        others = [c for c in stay if c["uid"] not in offline_uids]
        if sub_bins or others:
            plan.notes.append("La cartella «%s» non c'è più su disco, ma nel bin ci sono ancora "
                              "timeline o clip con il file altrove: lascio il bin." % b)
            continue
        plan.add_choice(
            "cartella-sparita:" + b,
            "La cartella «%s» è stata cancellata dal disco: %s nel bin ora %s offline."
            % (b, _plural(len(stay), "clip", "clip"), "è" if len(stay) == 1 else "sono"),
            [{"key": "lascia", "label": "Lascia il bin (clip offline)", "resolve": []},
             {"key": "togli", "label": "Togli bin e clip da Resolve",
              "resolve": [dict(kind="delete_clips", why="Tolgo «%s»" % c["name"], uid=c["uid"],
                               bin=b) for c in stay] + deletes}],
            default="lascia")
        offline_uids -= {c["uid"] for c in stay}     # già spiegate dalla domanda

    # ── 4. file non usati da nessuna clip ────────────────────────────────────
    linked_at = {}
    for uid, l in links.items():
        p = locate(uid, l)
        if p:
            linked_at[p] = uid
    trash_files, copying = [], []
    for p in sorted(disk.files):
        if p in used or under_trash(p):
            continue
        final = _remap(p, disk_moves)
        if p in linked_at:
            trash_files.append(final)
            plan.add("disk", "trash", "Clip tolta da Resolve → «%s» in _CESTINO" % final, path=final)
        elif now - disk.files[p]["mt"] < RECENT_SECONDS:
            copying.append(final)
        else:
            plan.add("resolve", "import", "File nuovo su disco «%s»" % final,
                     file=final, bin=target_bin(_dirname(final)))

    # ── 5. avvisi ────────────────────────────────────────────────────────────
    offline = [c for c in offline if c["uid"] in offline_uids]
    if offline:
        names = [c["name"] for c in offline]
        plan.notes.append("%s offline: il file non è più nella cartella (%s)."
                          % (_plural(len(names), "clip", "clip"),
                             ", ".join(names[:6]) + ("…" if len(names) > 6 else "")))
    if copying:
        plan.notes.append("%s modificat%s pochi secondi fa (forse ancora in copia): li importo "
                          "alla prossima analisi." % (_plural(len(copying), "file", "file"),
                                                      "o" if len(copying) == 1 else "i"))
    if plan.outside:
        plan.notes.append("%s il file fuori dalla cartella: non le tocco."
                          % _plural(plan.outside, "clip ha", "clip hanno"))
    if unmanaged:
        plan.notes.append("%s di sequenze o schede camera: non le tocco."
                          % _plural(unmanaged, "clip", "clip"))
    if trash_files and not force:
        limit = max(MAX_TRASH_FREE, int(len(links) * MAX_TRASH_RATIO))
        if len(trash_files) > limit:
            plan.blocked = ("Dovrei mettere %d file nel cestino perché le loro clip non sono più "
                            "in Resolve: sono tanti. Controlla che sia aperto il progetto giusto."
                            % len(trash_files))
            plan.can_force = True
    return plan
