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


def is_opaque(name):
    return name.upper() in OPAQUE_NAMES or name.lower().endswith(OPAQUE_SUFFIX)


class DiskSnapshot:
    """folders: rel -> id cartella ("dev:ino")
    files:   rel -> {"ino": "dev:ino", "fp": "size|mtime", "mt": mtime}
    opaque:  cartelle trattate come blocco unico (schede camera…)
    sequences: [(cartella, nome, quanti fotogrammi)] lasciate fuori
    I percorsi relativi usano sempre "/" e la radice è ""."""

    def __init__(self, root):
        self.root = root
        self.folders = {}
        self.files = {}
        self.opaque = []
        self.sequences = []
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


def _split_sequences(rel_dir, names):
    """Toglie dalla lista i fotogrammi delle sequenze; ritorna (resto, sequenze)."""
    groups = {}
    for fn in names:
        m = _SEQ_RE.match(fn)
        if m and m.group(3).lower() in SEQ_EXT:
            groups.setdefault((m.group(1), m.group(3).lower()), []).append(fn)
    seq_files, seqs = set(), []
    for (prefix, ext), members in groups.items():
        if len(members) >= SEQ_MIN:
            seq_files.update(members)
            seqs.append((rel_dir, prefix + "#" + ext, len(members)))
    return [n for n in names if n not in seq_files], seqs


def scan(root):
    snap = DiskSnapshot(root)
    snap.case_insensitive = _case_insensitive(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        keep = []
        for d in sorted(dirnames):
            if d.startswith(".") or (rel_dir == "" and d in IGNORED_DIRS):
                continue
            if is_opaque(d):
                snap.opaque.append((rel_dir + "/" + d) if rel_dir else d)
                continue
            keep.append(d)
        dirnames[:] = keep
        if rel_dir:
            snap.folders[rel_dir] = _ino(os.stat(dirpath))
        media = [fn for fn in filenames if not fn.startswith(".") and is_media(fn)]
        media, seqs = _split_sequences(rel_dir, media)
        snap.sequences += seqs
        for fn in media:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            snap.files[rel] = {"ino": _ino(st), "fp": "%d|%d" % (st.st_size, int(st.st_mtime)),
                               "mt": st.st_mtime}
    return snap


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
