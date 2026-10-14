"""Lato disco: fotografia della cartella radice e operazioni sui file.

Regola d'oro: un file vero non si cancella mai. Quello che "sparisce" finisce in
<radice>/_CESTINO/<data_ora>/<percorso originale>.
"""
import os
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


def is_media(name):
    return os.path.splitext(name)[1].lower() in MEDIA_EXT


class DiskSnapshot:
    """folders: rel -> id cartella ("dev:ino")
    files:   rel -> {"ino": "dev:ino", "fp": "size|mtime"}
    I percorsi relativi usano sempre "/" e la radice è ""."""

    def __init__(self, root):
        self.root = root
        self.folders = {}
        self.files = {}

    def files_in(self, folder):
        pre = folder + "/" if folder else ""
        return {p: r for p, r in self.files.items()
                if p.startswith(pre) and "/" not in p[len(pre):]}


def _ino(st):
    return "%d:%d" % (st.st_dev, st.st_ino)


def scan(root):
    snap = DiskSnapshot(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and not (rel_dir == "" and d in IGNORED_DIRS))
        if rel_dir:
            snap.folders[rel_dir] = _ino(os.stat(dirpath))
        for fn in filenames:
            if fn.startswith(".") or not is_media(fn):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            snap.files[rel] = {"ino": _ino(st), "fp": "%d|%d" % (st.st_size, int(st.st_mtime))}
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


class DiskOps:
    def __init__(self, root):
        self.root = root
        self.stamp = time.strftime("%Y-%m-%d_%H%M%S")

    def mkdir(self, rel):
        os.makedirs(abs_path(self.root, rel), exist_ok=True)
        return "creata"

    def move(self, src_rel, dst_rel):
        src, dst = abs_path(self.root, src_rel), abs_path(self.root, dst_rel)
        if not os.path.exists(src):
            # Già spostato insieme alla cartella madre.
            return "già fatto" if os.path.exists(dst) else "sorgente sparita"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
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
