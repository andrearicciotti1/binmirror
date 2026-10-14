"""Resolve finta per i test (da Linux, senza DaVinci).

Copre l'API che usa resolve_helper.py. Lo stato sta in un JSON (FAKE_RESOLVE_STATE)
perché ogni chiamata dell'helper è un processo nuovo, come con la fusionscript vera.
FAKE_NO_FOLDER_UID=1 simula una versione di Resolve senza Folder.GetUniqueId.
I test usano le stesse classi per fare "a mano" quello che farebbe il montatore.
"""
import json
import os

STATE = os.environ.get("FAKE_RESOLVE_STATE", "")


def _new_state():
    return {"next": 1, "root": {"name": "Master", "uid": "F0", "children": [], "clips": []}}


class Ctx:
    def __init__(self):
        try:
            with open(STATE) as f:
                self.st = json.load(f)
        except (OSError, ValueError):
            self.st = _new_state()

    def uid(self, prefix):
        n = self.st["next"]
        self.st["next"] += 1
        return "%s%d" % (prefix, n)

    def save(self):
        with open(STATE, "w") as f:
            json.dump(self.st, f, indent=1)

    def parent_of(self, node, cur=None):
        cur = cur or self.st["root"]
        for c in cur["children"]:
            if c is node:
                return cur
            hit = self.parent_of(node, c)
            if hit:
                return hit
        return None

    def bin_of(self, clip, cur=None):
        cur = cur or self.st["root"]
        if any(c is clip for c in cur["clips"]):
            return cur
        for c in cur["children"]:
            hit = self.bin_of(clip, c)
            if hit:
                return hit
        return None


class Folder:
    def __init__(self, ctx, node):
        self.ctx, self.node = ctx, node

    def GetName(self):
        return self.node["name"]

    def GetUniqueId(self):
        if os.environ.get("FAKE_NO_FOLDER_UID"):
            raise TypeError("'NoneType' object is not callable")
        return self.node["uid"]

    def GetSubFolderList(self):
        return [Folder(self.ctx, c) for c in self.node["children"]]

    def GetClipList(self):
        return [MediaPoolItem(self.ctx, c) for c in self.node["clips"]]


class MediaPoolItem:
    def __init__(self, ctx, clip):
        self.ctx, self.clip = ctx, clip

    def GetUniqueId(self):
        return self.clip["uid"]

    def GetName(self):
        return self.clip["name"]

    def GetClipProperty(self, key=None):
        return self.clip.get("file", "") if key == "File Path" else ""

    def ReplaceClip(self, path):
        if not os.path.isfile(path):
            return False
        self.clip["file"] = path
        self.ctx.save()
        return True


class MediaPool:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cur = self.GetRootFolder()

    def GetRootFolder(self):
        return Folder(self.ctx, self.ctx.st["root"])

    def AddSubFolder(self, parent, name):
        node = {"name": name, "uid": self.ctx.uid("F"), "children": [], "clips": []}
        parent.node["children"].append(node)
        self.ctx.save()
        return Folder(self.ctx, node)

    def SetCurrentFolder(self, folder):
        self.cur = folder
        return True

    def ImportMedia(self, paths):
        out = []
        for p in paths:
            if not os.path.isfile(p):
                continue
            clip = {"uid": self.ctx.uid("C"), "file": p, "name": os.path.basename(p)}
            self.cur.node["clips"].append(clip)
            out.append(MediaPoolItem(self.ctx, clip))
        self.ctx.save()
        return out

    def MoveClips(self, items, target):
        for it in items:
            src = self.ctx.bin_of(it.clip)
            src["clips"] = [c for c in src["clips"] if c is not it.clip]
            target.node["clips"].append(it.clip)
        self.ctx.save()
        return True

    def MoveFolders(self, folders, target):
        for f in folders:
            par = self.ctx.parent_of(f.node)
            par["children"] = [c for c in par["children"] if c is not f.node]
            target.node["children"].append(f.node)
        self.ctx.save()
        return True

    def DeleteFolders(self, folders):
        for f in folders:
            par = self.ctx.parent_of(f.node)
            par["children"] = [c for c in par["children"] if c is not f.node]
        self.ctx.save()
        return True

    def DeleteClips(self, items):
        for it in items:
            src = self.ctx.bin_of(it.clip)
            src["clips"] = [c for c in src["clips"] if c is not it.clip]
        self.ctx.save()
        return True

    def RelinkClips(self, items, folder):
        ok = True
        for it in items:
            cand = os.path.join(folder, os.path.basename(it.clip["file"]))
            if os.path.isfile(cand):
                it.clip["file"] = cand
            else:
                ok = False
        self.ctx.save()
        return ok


class Project:
    def __init__(self, ctx):
        self.ctx = ctx

    def GetMediaPool(self):
        return MediaPool(self.ctx)

    def GetName(self):
        return "Progetto di prova"


class ProjectManager:
    def __init__(self, ctx):
        self.ctx = ctx

    def GetCurrentProject(self):
        return Project(self.ctx)


class Resolve:
    def __init__(self):
        self.ctx = Ctx()

    def GetProjectManager(self):
        return ProjectManager(self.ctx)


def scriptapp(name, host=None):
    if os.environ.get("FAKE_RESOLVE_MODE") == "down" or name != "Resolve":
        return None
    return Resolve()
