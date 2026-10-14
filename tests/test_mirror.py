"""Scenari da montatore, con una Resolve finta e un disco vero (cartella temporanea).

    python3 -m unittest discover -s tests -v
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from binmirror import disk as diskmod  # noqa: E402
from binmirror import engine, planner  # noqa: E402
from binmirror.__main__ import main as cli  # noqa: E402

FAKE = os.path.join(HERE, "fake_fusionscript.py")


def load_fake():
    spec = importlib.util.spec_from_file_location("fake_fs_test", FAKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Studio:
    """Il montatore: tocca il disco e la Resolve finta come farebbe a mano."""

    def __init__(self, root):
        self.root = root
        self.fake = load_fake()

    def pool(self):
        return self.fake.Resolve().GetProjectManager().GetCurrentProject().GetMediaPool()

    def folder(self, mp, path):
        f = mp.GetRootFolder()
        for name in [p for p in path.split("/") if p]:
            f = [s for s in f.GetSubFolderList() if s.GetName() == name][0]
        return f

    def clip(self, mp, name):
        def rec(f):
            for c in f.GetClipList():
                if c.GetName() == name:
                    return c
            for s in f.GetSubFolderList():
                hit = rec(s)
                if hit:
                    return hit
        return rec(mp.GetRootFolder())

    # Resolve
    def add_bin(self, path):
        mp = self.pool()
        parent, _, name = path.rpartition("/")
        mp.AddSubFolder(self.folder(mp, parent), name)

    def import_file(self, rel, bin_path=""):
        mp = self.pool()
        mp.SetCurrentFolder(self.folder(mp, bin_path))
        mp.ImportMedia([os.path.join(self.root, rel)])

    def move_clip(self, name, bin_path):
        mp = self.pool()
        mp.MoveClips([self.clip(mp, name)], self.folder(mp, bin_path))

    def delete_clip(self, name):
        mp = self.pool()
        mp.DeleteClips([self.clip(mp, name)])

    def rename_bin(self, path, new_name):
        mp = self.pool()
        self.folder(mp, path).node["name"] = new_name
        mp.ctx.save()

    def delete_bin(self, path):
        mp = self.pool()
        mp.DeleteFolders([self.folder(mp, path)])

    def bins(self):
        out = []

        def rec(f, p):
            for s in f.GetSubFolderList():
                q = (p + "/" + s.GetName()) if p else s.GetName()
                out.append(q)
                rec(s, q)
        rec(self.pool().GetRootFolder(), "")
        return sorted(out)

    def clip_info(self, name):
        mp = self.pool()
        c = self.clip(mp, name)
        if c is None:
            return None
        b = mp.ctx.bin_of(c.clip)
        path = []
        while b is not mp.ctx.st["root"]:
            path.insert(0, b["name"])
            b = mp.ctx.parent_of(b)
        return "/".join(path), os.path.relpath(c.clip["file"], self.root)

    # disco
    def write(self, rel, data=b"x"):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def mv(self, a, b):
        dst = os.path.join(self.root, b)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(os.path.join(self.root, a), dst)

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def trashed(self):
        out = []
        for dp, _, fns in os.walk(os.path.join(self.root, "_CESTINO")):
            out += fns
        return sorted(out)


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "MATERIALI")
        os.makedirs(self.root)
        os.environ["FAKE_RESOLVE_STATE"] = os.path.join(self.tmp, "resolve.json")
        os.environ["RESOLVE_SCRIPT_LIB"] = FAKE
        os.environ["BINMIRROR_PYTHON"] = sys.executable
        os.environ.pop("FAKE_NO_FOLDER_UID", None)
        planner.RECENT_SECONDS = 0
        self.s = Studio(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def sync(self, expect_blocked=False, selections=None, force=False):
        plan = engine.analyze(self.root, force=force)
        if expect_blocked:
            self.assertIsNotNone(plan.blocked)
            return plan
        self.assertIsNone(plan.blocked, plan.blocked)
        engine.apply(self.root, plan, selections=selections)
        again = engine.analyze(self.root)
        self.assertTrue(again.is_empty(), "dopo la sync resta da fare: %s %s"
                        % (again.disk, again.resolve))
        return plan

    def base_project(self):
        """Disco: INTERVISTE/int1.mov, MUSICA/brano.wav. Resolve: bin B-ROLL, int1 importata in Master."""
        self.s.write("INTERVISTE/int1.mov", b"aaa")
        self.s.write("MUSICA/brano.wav", b"bbbb")
        self.s.add_bin("B-ROLL")
        self.s.import_file("INTERVISTE/int1.mov", "")
        self.sync()

    # ── scenari ──
    def test_prima_sync_unisce(self):
        self.base_project()
        self.assertEqual(self.s.bins(), ["B-ROLL", "INTERVISTE", "MUSICA"])
        self.assertTrue(self.s.exists("B-ROLL"))
        self.assertEqual(self.s.clip_info("int1.mov"), ("INTERVISTE", "INTERVISTE/int1.mov"))
        self.assertEqual(self.s.clip_info("brano.wav"), ("MUSICA", "MUSICA/brano.wav"))
        self.assertEqual(self.s.trashed(), [])

    def test_clip_spostata_in_resolve_sposta_il_file(self):
        self.base_project()
        self.s.move_clip("int1.mov", "B-ROLL")
        self.sync()
        self.assertTrue(self.s.exists("B-ROLL/int1.mov"))
        self.assertFalse(self.s.exists("INTERVISTE/int1.mov"))
        self.assertEqual(self.s.clip_info("int1.mov"), ("B-ROLL", "B-ROLL/int1.mov"))

    def test_file_spostato_su_disco_sposta_la_clip(self):
        self.base_project()
        self.s.mv("MUSICA/brano.wav", "INTERVISTE/brano.wav")
        self.sync()
        self.assertEqual(self.s.clip_info("brano.wav"), ("INTERVISTE", "INTERVISTE/brano.wav"))

    def test_cartella_rinominata_su_disco(self):
        self.base_project()
        self.s.mv("INTERVISTE", "INTERVISTE_DEF")
        self.sync()
        self.assertIn("INTERVISTE_DEF", self.s.bins())
        self.assertNotIn("INTERVISTE", self.s.bins())
        self.assertEqual(self.s.clip_info("int1.mov"), ("INTERVISTE_DEF", "INTERVISTE_DEF/int1.mov"))

    def test_bin_rinominato_in_resolve(self):
        self.base_project()
        self.s.rename_bin("MUSICA", "SOUND")
        self.sync()
        self.assertTrue(self.s.exists("SOUND/brano.wav"))
        self.assertFalse(self.s.exists("MUSICA"))
        self.assertEqual(self.s.clip_info("brano.wav"), ("SOUND", "SOUND/brano.wav"))

    def test_bin_annidato_e_file_nuovo(self):
        self.base_project()
        self.s.add_bin("B-ROLL/DRONE")
        self.s.write("B-ROLL/DRONE/volo.mp4")
        self.s.write("B-ROLL/DRONE/note.txt")      # non è un media: ignorato
        self.sync()
        self.assertEqual(self.s.clip_info("volo.mp4"), ("B-ROLL/DRONE", "B-ROLL/DRONE/volo.mp4"))
        self.assertIsNone(self.s.clip_info("note.txt"))

    def test_clip_tolta_va_nel_cestino(self):
        self.base_project()
        self.s.delete_clip("brano.wav")
        self.sync()
        self.assertFalse(self.s.exists("MUSICA/brano.wav"))
        self.assertEqual(self.s.trashed(), ["brano.wav"])

    def test_bin_tolto_va_nel_cestino(self):
        self.base_project()
        self.s.delete_bin("MUSICA")
        plan = engine.analyze(self.root)
        self.assertEqual([c["default"] for c in plan.choices], ["cestino"])
        self.sync()
        self.assertFalse(self.s.exists("MUSICA"))
        self.assertEqual(self.s.trashed(), ["brano.wav"])

    def test_cartella_vuota_sparita_toglie_il_bin(self):
        self.base_project()
        os.rmdir(os.path.join(self.root, "B-ROLL"))
        self.sync()
        self.assertNotIn("B-ROLL", self.s.bins())

    def test_disco_scollegato_blocca(self):
        self.base_project()
        shutil.rmtree(self.root)
        self.sync(expect_blocked=True)

    def test_disco_vuoto_blocca(self):
        self.base_project()
        for d in ("INTERVISTE", "MUSICA"):
            shutil.rmtree(os.path.join(self.root, d))
        self.sync(expect_blocked=True)

    def test_senza_id_dei_bin_non_cestina_cartelle(self):
        os.environ["FAKE_NO_FOLDER_UID"] = "1"
        self.base_project()
        self.s.delete_bin("B-ROLL")
        plan = engine.analyze(self.root)
        self.assertFalse(any(a["kind"] == "trash" for a in plan.disk))
        self.assertTrue(plan.notes)

    def test_file_fuori_radice_non_toccato(self):
        self.base_project()
        fuori = os.path.join(self.tmp, "fuori.mov")
        with open(fuori, "wb") as f:
            f.write(b"z")
        mp = self.s.pool()
        mp.SetCurrentFolder(self.s.folder(mp, "B-ROLL"))
        mp.ImportMedia([fuori])
        plan = self.sync()
        self.assertEqual(plan.outside, 1)
        self.assertEqual(self.s.clip_info("fuori.mov")[0], "B-ROLL")

    def test_collisione_di_nome(self):
        self.base_project()
        self.s.write("B-ROLL/int1.mov", b"diverso")
        self.s.move_clip("int1.mov", "B-ROLL")
        # Il file nuovo in B-ROLL viene importato; la clip spostata porta il suo file accanto.
        self.sync()
        self.assertTrue(self.s.exists("B-ROLL/int1_1.mov"))
        bins = {self.s.clip_info("int1.mov")[1]}
        self.assertTrue(bins & {"B-ROLL/int1_1.mov", "B-ROLL/int1.mov"})

    # ── casi limite ──
    def test_cartella_cancellata_chiede_e_di_default_lascia(self):
        self.base_project()
        shutil.rmtree(os.path.join(self.root, "MUSICA"))
        plan = engine.analyze(self.root)
        self.assertEqual(len(plan.choices), 1)
        self.assertIn("cancellata dal disco", plan.choices[0]["question"])
        self.sync()
        self.assertIn("MUSICA", self.s.bins())          # bin lasciato, clip offline
        self.assertFalse(self.s.exists("MUSICA"))        # e la cartella NON viene ricreata
        again = engine.analyze(self.root)
        self.assertFalse(again.choices)                  # non lo richiede ogni volta

    def test_cartella_cancellata_togli_bin_e_clip(self):
        self.base_project()
        shutil.rmtree(os.path.join(self.root, "MUSICA"))
        self.sync(selections={"cartella-sparita:MUSICA": "togli"})
        self.assertNotIn("MUSICA", self.s.bins())
        self.assertIsNone(self.s.clip_info("brano.wav"))

    def test_cartella_cancellata_ma_file_spostati(self):
        self.base_project()
        self.s.mv("MUSICA/brano.wav", "B-ROLL/brano.wav")
        os.rmdir(os.path.join(self.root, "MUSICA"))
        plan = self.sync()
        self.assertFalse(plan.choices)
        self.assertNotIn("MUSICA", self.s.bins())
        self.assertEqual(self.s.clip_info("brano.wav"), ("B-ROLL", "B-ROLL/brano.wav"))

    def test_cartella_annidata_cancellata(self):
        self.base_project()
        self.s.add_bin("B-ROLL/DRONE")
        self.s.write("B-ROLL/DRONE/volo.mp4")
        self.sync()
        shutil.rmtree(os.path.join(self.root, "B-ROLL"))
        plan = engine.analyze(self.root)
        self.assertEqual([c["id"] for c in plan.choices], ["cartella-sparita:B-ROLL"])
        self.sync(selections={"cartella-sparita:B-ROLL": "togli"})
        self.assertNotIn("B-ROLL", self.s.bins())
        self.assertNotIn("B-ROLL/DRONE", self.s.bins())

    def test_bin_tolto_ripristina(self):
        self.base_project()
        self.s.delete_bin("MUSICA")
        self.sync(selections={"bin-tolto:MUSICA": "ripristina"})
        self.assertEqual(self.s.clip_info("brano.wav"), ("MUSICA", "MUSICA/brano.wav"))
        self.assertEqual(self.s.trashed(), [])

    def test_progetto_diverso_blocca(self):
        self.base_project()
        st = engine.load_state(self.root)
        st["project"] = "Un altro film"
        engine.save_state(self.root, st)
        plan = self.sync(expect_blocked=True)
        self.assertTrue(plan.can_force)
        self.assertIn("Un altro film", plan.blocked)
        self.sync(force=True)       # forzato = prima sincronizzazione, niente cestino
        self.assertEqual(self.s.trashed(), [])

    def test_disco_esterno_non_collegato(self):
        plan = engine.analyze("/Volumes/DISCO_CHE_NON_CE/MATERIALI")
        self.assertIn("«DISCO_CHE_NON_CE» non è collegato", plan.blocked)

    def test_file_ancora_in_copia_non_importato(self):
        self.base_project()
        planner.RECENT_SECONDS = 60
        self.s.write("MUSICA/nuovo.wav", b"in copia")
        plan = engine.analyze(self.root)
        self.assertFalse(any(a["kind"] == "import" for a in plan.resolve))
        self.assertTrue(any("ancora in copia" in n for n in plan.notes))

    def test_sequenze_e_schede_camera(self):
        for i in range(1, 8):
            self.s.write("VFX/shot010_%04d.exr" % i)
        self.s.write("RED/A001_C001.RDC/A001_C001_001.R3D")
        self.s.write("RED/A001_C001.RDC/A001_C001_002.R3D")
        plan = self.sync()
        self.assertIn("VFX", self.s.bins())
        self.assertNotIn("RED/A001_C001.RDC", self.s.bins())
        self.assertFalse(any(a["kind"] == "import" for a in plan.resolve))
        self.assertTrue(any("Sequenza" in n for n in plan.notes))
        self.assertTrue(any("scheda camera" in n for n in plan.notes))

    def test_bin_con_slash_e_doppioni(self):
        self.base_project()
        mp = self.s.pool()
        mp.AddSubFolder(mp.GetRootFolder(), "A/B")       # nome con la /
        mp = self.s.pool()
        mp.AddSubFolder(mp.GetRootFolder(), "MUSICA")    # secondo bin MUSICA
        plan = self.sync()
        self.assertTrue(any("/ nel nome" in n for n in plan.notes))
        self.assertTrue(any("due bin «MUSICA»" in n for n in plan.notes))
        self.assertFalse(self.s.exists("A"))

    def test_maiuscole_su_disco_che_non_le_distingue(self):
        self.s.write("MUSICA/brano.wav", b"bbbb")
        self.s.add_bin("Musica")
        real_scan = diskmod.scan

        def scan_ci(root):
            snap = real_scan(root)
            snap.case_insensitive = True
            return snap
        engine.diskmod.scan = scan_ci
        try:
            plan = engine.analyze(self.root)
        finally:
            engine.diskmod.scan = real_scan
        self.assertFalse(any(a["kind"] == "mkdir" for a in plan.disk))
        self.assertFalse(any(a["kind"] == "create_bin" for a in plan.resolve))
        self.assertTrue(any("maiuscole" in n for n in plan.notes))
        self.assertEqual([a["bin"] for a in plan.resolve if a["kind"] == "import"], ["Musica"])

    def test_unione_cartelle_su_disco(self):
        self.s.write("A/x.mov", b"1")
        self.s.write("B/x.mov", b"2")
        self.s.write("B/y.mov", b"3")
        out = diskmod.DiskOps(self.root).move("A", "B", folder=True)
        self.assertTrue(out.startswith("unita"))
        self.assertFalse(self.s.exists("A"))
        self.assertTrue(self.s.exists("B/x_1.mov"))

    def test_piano_cambiato_tra_analizza_e_applica(self):
        self.base_project()
        plan = engine.analyze(self.root)
        self.s.write("MUSICA/altro.wav", b"zz")
        with self.assertRaises(engine.PlanChanged):
            engine.apply(self.root, plan)

    def test_due_sincronizzazioni_insieme(self):
        self.base_project()
        lock = os.path.join(self.root, ".binmirror", "lock")
        with open(lock, "w") as f:
            f.write(str(os.getppid()))      # un processo vivo che non sono io
        self.s.write("MUSICA/altro.wav", b"zz")
        plan = engine.analyze(self.root)
        with self.assertRaises(engine.Busy):
            engine.apply(self.root, plan)
        os.remove(lock)

    def test_operazioni_a_gruppi(self):
        for i in range(6):
            self.s.write("CAM_A/clip_%d.mov" % i, b"x" * (i + 1))
        self.sync()
        with open(os.environ["FAKE_RESOLVE_STATE"]) as f:
            calls = json.load(f)["calls"]
        self.assertEqual(calls["ImportMedia"], 1)       # 6 file, una sola chiamata

    def test_cli_analizza(self):
        self.s.write("A/x.mov")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli(["analizza", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("File nuovo su disco «A/x.mov»", buf.getvalue())

    def test_resolve_chiusa(self):
        os.environ["FAKE_RESOLVE_MODE"] = "down"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli(["analizza", self.root])
            self.assertEqual(rc, 2)
            self.assertIn("non raggiungibile", buf.getvalue())
        finally:
            os.environ.pop("FAKE_RESOLVE_MODE")


if __name__ == "__main__":
    unittest.main()
