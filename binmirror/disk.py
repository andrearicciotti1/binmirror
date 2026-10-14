"""Lato disco: fotografia della cartella radice e operazioni sui file.

Regola d'oro: un file vero non si cancella mai. Quello che "sparisce" finisce in
<radice>/_CESTINO/<data_ora>/<percorso originale>.
"""
import os
import re
import shutil
import time

TRASH_DIR = "_CESTINO"
STATE_DIR = ".binmirror"
IGNORED_DIRS = {TRASH_DIR, STATE_DIR}
# Cartelle ignorate di default: quelle appena create dal Finder, prima che le rinomini.
DEFAULT_IGNORE = ["untitled folder", "cartella senza titolo"]
TIMELINE_EXT = {".drt", ".xml", ".fcpxml", ".otio"}
# Resolve 21 esporta l'FCPXML 1.10 come pacchetto: cartella «nome.fcpxml» con dentro
# Info.fcpxml. Non è un bin: è una timeline (si importa il file Info.fcpxml).
TIMELINE_BUNDLE_EXT = (".fcpxml", ".fcpxmld")
BUNDLE_INFO = "Info.fcpxml"

# Estensioni importabili in Resolve. Tutto il resto (pdf, txt, progetti…) viene ignorato.
MEDIA_EXT = {
    # video
    ".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".mts", ".m2ts", ".braw", ".r3d",
    ".ari", ".crm", ".dng", ".mpg", ".mpeg", ".webm", ".3gp",
    # audio
    ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".bwf", ".ogg",
    # immagini
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".psd", ".heic", ".bmp", ".gif",
}

# Formati che di solito arrivano come sequenze di fotogrammi (shot_0001.exr, shot_0002.exr…).
SEQ_EXT = {".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".dng", ".cin"}
SEQ_MIN = 5
_SEQ_RE = re.compile(r"^(.*?)(\d{3,})(\.[^.]+)$")

# Strutture di scheda camera o pacchetti: si trattano come un blocco unico, senza bin
# per ogni sottocartella e senza importare i singoli pezzi.
OPAQUE_NAMES = {"BDMV", "PRIVATE", "XDROOT", "M4ROOT", "AVCHD", "CONTENTS"}
OPAQUE_SUFFIX = (".rdc", ".rdm", ".fcpbundle", ".app", ".photoslibrary", ".dvcc")


def is_media(name):
    return os.path.splitext(name)[1].lower() in MEDIA_EXT


def _ext(e):
    e = e.strip().lower()
    return e if e.startswith(".") else "." + e


class Filters:
    """Quali file e cartelle rispecchiare.

    extensions: lista di estensioni ammesse (vuota = MEDIA_EXT).
    ignore: nomi di cartella (maiuscole indifferenti) o regex tra slash, es. "/_AME/",
            cercata nel percorso relativo della cartella."""

    def __init__(self, extensions=None, ignore=None):
        self.exts = {_ext(e) for e in (extensions or []) if e.strip()} or set(MEDIA_EXT)
        self.names = {n.lower() for n in IGNORED_DIRS}
        self.regexes = []
        self._under = {}
        for item in DEFAULT_IGNORE if ignore is None else ignore:
            item = item.strip()
            if len(item) > 2 and item.startswith("/") and item.endswith("/"):
                try:
                    self.regexes.append(re.compile(item[1:-1], re.I))
                except re.error:
                    pass
            elif item:
                self.names.add(item.lower())

    def allows(self, name):
        return os.path.splitext(name)[1].lower() in self.exts

    def ignores_dir(self, rel):
        name = rel.rsplit("/", 1)[-1]
        if name.startswith(".") or name.lower() in self.names:
            return True
        return any(r.search(rel) for r in self.regexes)

    def ignores_under(self, rel):
        """True se rel o una delle cartelle che la contengono è ignorata."""
        if rel not in self._under:
            parent = rel.rpartition("/")[0]
            self._under[rel] = bool(rel) and (self.ignores_dir(rel) or self.ignores_under(parent))
        return self._under[rel]


def is_opaque(name):
    return name.upper() in OPAQUE_NAMES or name.lower().endswith(OPAQUE_SUFFIX)


class DiskSnapshot:
    """folders: rel -> id cartella ("dev:ino")
    files:   rel -> {"ino": "dev:ino", "fp": "size|mtime", "mt": mtime}
    opaque:  cartelle trattate come blocco unico (schede camera…)
    sequences: key -> sequenza di immagini (vedi _split_sequences)
    timelines: rel -> {"ino", "fp"} file .drt/.xml/.fcpxml/.otio
    I percorsi relativi usano sempre "/" e la radice è ""."""

    def __init__(self, root):
        self.root = root
        self.folders = {}
        self.files = {}
        self.opaque = []
        self.sequences = {}
        self.timelines = {}
        self.filters = Filters()
        self.case_insensitive = False

    def files_in(self, folder):
        pre = folder + "/" if folder else ""
        return {p: r for p, r in self.files.items()
                if p.startswith(pre) and "/" not in p[len(pre):]}

    def files_under(self, folder):
        pre = folder + "/" if folder else ""
        return [p for p in self.files if p.startswith(pre)]


def _ino(st):
    return "%d:%d" % (st.st_dev, st.st_ino)


def _case_insensitive(root):
    """True se il file system ignora le maiuscole (APFS e HFS+ di default sul Mac)."""
    head, tail = os.path.split(os.path.abspath(root))
    other = tail.swapcase()
    if other == tail:
        return False
    try:
        return os.path.samefile(root, os.path.join(head, other))
    except OSError:
        return False


def seq_key(rel_dir, prefix, ext):
    """Identità di una sequenza: cartella + prefisso + estensione (senza maiuscole)."""
    return ((rel_dir + "/") if rel_dir else "") + prefix + "#" + ext.lower()


def _split_sequences(dirpath, rel_dir, names):
    """Toglie dalla lista i fotogrammi delle sequenze; ritorna (resto, {key: sequenza}).

    Una sequenza: {"dir", "prefix", "ext", "pad", "start", "end", "prev", "count", "mt"}.
    end è l'ultimo fotogramma, prev il penultimo, mt la data di modifica dell'ultimo:
    con un render in corso l'ultimo fotogramma può essere ancora a metà."""
    groups = {}
    for fn in names:
        m = _SEQ_RE.match(fn)
        if m and m.group(3).lower() in SEQ_EXT:
            groups.setdefault((m.group(1), m.group(3).lower()), []).append(
                (int(m.group(2)), m.group(2), fn))
    seq_files, seqs = set(), {}
    for (prefix, ext), members in groups.items():
        if len(members) < SEQ_MIN:
            continue
        members.sort()
        seq_files.update(fn for _, _, fn in members)
        try:
            mt = os.stat(os.path.join(dirpath, members[-1][2])).st_mtime
        except OSError:
            mt = 0
        seqs[seq_key(rel_dir, prefix, ext)] = {
            "dir": rel_dir, "prefix": prefix, "ext": os.path.splitext(members[0][2])[1],
            "pad": len(members[0][1]), "start": members[0][0], "end": members[-1][0],
            "prev": members[-2][0], "count": len(members), "mt": mt}
    return [n for n in names if n not in seq_files], seqs


def scan(root, filters=None):
    filters = filters or Filters()
    snap = DiskSnapshot(root)
    snap.filters = filters
    snap.case_insensitive = _case_insensitive(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        keep = []
        for d in sorted(dirnames):
            rel = (rel_dir + "/" + d) if rel_dir else d
            if filters.ignores_dir(rel):
                continue
            if d.lower().endswith(TIMELINE_BUNDLE_EXT):
                try:
                    ist = os.stat(os.path.join(dirpath, d, BUNDLE_INFO))
                    snap.timelines[rel] = {
                        "ino": _ino(os.stat(os.path.join(dirpath, d))),
                        "fp": "%d|%d" % (ist.st_size, int(ist.st_mtime)), "mt": ist.st_mtime}
                except OSError:
                    pass
                continue
            if is_opaque(d):
                snap.opaque.append(rel)
                continue
            keep.append(d)
        dirnames[:] = keep
        if rel_dir:
            snap.folders[rel_dir] = _ino(os.stat(dirpath))
        media, tls = [], []
        for fn in filenames:
            if fn.startswith("."):
                continue
            # prima le timeline: anche se l'utente mette xml tra le estensioni ammesse,
            # un .xml/.drt non è un media e ImportMedia lo rifiuterebbe.
            if os.path.splitext(fn)[1].lower() in TIMELINE_EXT:
                tls.append(fn)
            elif filters.allows(fn):
                media.append(fn)
        media, seqs = _split_sequences(dirpath, rel_dir, media)
        snap.sequences.update(seqs)
        for fn in media + tls:
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            rec = {"ino": _ino(st), "fp": "%d|%d" % (st.st_size, int(st.st_mtime)),
                   "mt": st.st_mtime}
            (snap.timelines if fn in tls else snap.files)[rel] = rec
    return snap


def signature(snap):
    """Impronta di cosa c'è su disco, per capire se dall'ultimo giro è cambiato qualcosa."""
    return hash((frozenset((p, r["fp"]) for p, r in snap.files.items()),
                 frozenset(snap.folders),
                 frozenset((k, s["end"], s["mt"]) for k, s in snap.sequences.items()),
                 frozenset((p, r["fp"]) for p, r in snap.timelines.items())))


# ── operazioni ────────────────────────────────────────────────────────────────

def abs_path(root, rel):
    return os.path.join(root, *rel.split("/")) if rel else root


def free_name(path):
    """Se path esiste già, aggiunge _1, _2… prima dell'estensione."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists("%s_%d%s" % (base, n, ext)):
        n += 1
    return "%s_%d%s" % (base, n, ext)


def _merge_dir(src, dst):
    """Sposta il contenuto di src dentro dst (che esiste già), poi toglie src se è vuota."""
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s) and os.path.isdir(d):
            _merge_dir(s, d)
        else:
            shutil.move(s, free_name(d))
    leftovers = [n for n in os.listdir(src) if n != ".DS_Store"]
    if not leftovers:
        shutil.rmtree(src, ignore_errors=True)


class DiskOps:
    def __init__(self, root):
        self.root = root
        self.stamp = time.strftime("%Y-%m-%d_%H%M%S")

    def mkdir(self, rel):
        os.makedirs(abs_path(self.root, rel), exist_ok=True)
        return "creata"

    def move(self, src_rel, dst_rel, folder=False):
        src, dst = abs_path(self.root, src_rel), abs_path(self.root, dst_rel)
        if not os.path.exists(src):
            # Già spostato insieme alla cartella madre.
            return "già fatto" if os.path.exists(dst) else "sorgente sparita"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if folder and os.path.isdir(dst) and not os.path.samefile(src, dst):
            _merge_dir(src, dst)
            return "unita a " + dst_rel
        if os.path.exists(dst) and os.path.samefile(src, dst):
            # Solo maiuscole diverse su un disco che non le distingue: passo da un nome provvisorio.
            tmp = free_name(src + "_binmirror_tmp")
            os.rename(src, tmp)
            os.rename(tmp, dst)
            return dst_rel
        final = free_name(dst)
        shutil.move(src, final)
        return os.path.relpath(final, self.root).replace(os.sep, "/")

    def trash(self, rel):
        src = abs_path(self.root, rel)
        if not os.path.exists(src):
            return "già sparito"
        dst = free_name(abs_path(self.root, TRASH_DIR + "/" + self.stamp + "/" + rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return os.path.relpath(dst, self.root).replace(os.sep, "/")
