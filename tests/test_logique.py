"""Ce qui ne doit jamais bouger sans qu'on le veuille : où vont les copies, quand le
garde-fou bloque, et ce que la mise à jour accepte d'installer.

    python -m unittest discover -s tests -v

Bibliothèque standard seule, comme l'application. Rien n'est lu ni écrit hors d'un
dossier temporaire : APPDATA est détourné avant de charger le script.
"""

import hashlib
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_appdata = tempfile.TemporaryDirectory()
os.environ["APPDATA"] = _appdata.name

_script = Path(__file__).resolve().parent.parent / "prisma_safe.pyw"
_loader = importlib.machinery.SourceFileLoader("prisma_safe", str(_script))
se = importlib.util.module_from_spec(importlib.util.spec_from_loader("prisma_safe", _loader))
_loader.exec_module(se)


def remplir(dossier, noms):
    for nom in noms:
        fichier = Path(dossier) / nom
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(b"x")


class Destinations(unittest.TestCase):
    def test_trois_dossiers_sans_niveau_ajoute(self):
        cfg = {"src_hd": r"C:\K\depot", "src_redim": r"C:\K\redim", "src_data": r"C:\K\data",
               "dest": r"R:\TRI"}
        jobs = se.build_jobs(cfg, "2026-10-12LEMANS")
        self.assertEqual([j[0] for j in jobs], ["HD", "REDIM", "DATA"])
        self.assertEqual([str(j[1]) for j in jobs],
                         [r"C:\K\depot\2026-10-12LEMANS", r"C:\K\redim\2026-10-12LEMANS", r"C:\K\data"])
        self.assertEqual([str(j[2]) for j in jobs],
                         [r"R:\TRI\2026-10-12LEMANS_HD", r"R:\TRI\2026-10-12LEMANS_REDIM",
                          r"R:\TRI\2026-10-12LEMANS_DATA"])


class GardeFou(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.src, self.dst = Path(self._tmp.name) / "src", Path(self._tmp.name) / "dst"
        self.src.mkdir()
        self.dst.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_source_videe_bloque(self):
        remplir(self.dst, [f"a/{i}.jpg" for i in range(40)])
        remplir(self.src, ["a/0.jpg", "a/1.jpg"])
        self.assertEqual(se.check_suppression(self.src, self.dst), (True, 38, 40))

    def test_pile_80_pour_cent_passe(self):
        remplir(self.dst, [f"{i}.jpg" for i in range(10)])
        remplir(self.src, ["0.jpg", "1.jpg"])  # 8 sur 10 : le seuil est « plus de 80 % »
        self.assertEqual(se.check_suppression(self.src, self.dst), (False, 8, 10))

    def test_au_dela_de_80_pour_cent_bloque(self):
        remplir(self.dst, [f"{i}.jpg" for i in range(10)])
        remplir(self.src, ["0.jpg"])
        self.assertEqual(se.check_suppression(self.src, self.dst), (True, 9, 10))

    def test_peu_de_photos_ne_bloque_pas(self):
        remplir(self.dst, [f"{i}.jpg" for i in range(9)])
        self.assertEqual(se.check_suppression(self.src, self.dst), (False, 9, 9))

    def test_rien_de_sauvegarde_ne_bloque_pas(self):
        remplir(self.src, ["0.jpg"])
        self.assertEqual(se.check_suppression(self.src, self.dst), (False, 0, 0))

    def test_seules_les_photos_comptent(self):
        remplir(self.dst, [f"{i}.jpg" for i in range(10)] + [f"{i}.xmp" for i in range(90)])
        remplir(self.src, [f"{i}.jpg" for i in range(10)])  # les .xmp partis ne sont pas des photos
        self.assertEqual(se.check_suppression(self.src, self.dst), (False, 0, 10))

    def test_sans_photo_on_compte_tous_les_fichiers(self):
        remplir(self.dst, [f"{i}.json" for i in range(20)])
        self.assertEqual(se.check_suppression(self.src, self.dst), (True, 20, 20))

    def test_la_casse_du_nom_ne_compte_pas(self):
        remplir(self.dst, [f"A/{i}.JPG" for i in range(20)])
        remplir(self.src, [f"a/{i}.jpg" for i in range(20)])
        self.assertEqual(se.check_suppression(self.src, self.dst), (False, 0, 20))


def fiche(tag, actifs):
    return {"tag_name": tag, "assets": actifs}


def actif(nom="Prisma.Safe.exe", url=None, **plus):
    url = url or f"https://github.com/hlelouarn/prisma-safe/releases/download/v9.9.9/{nom}"
    return {"name": nom, "browser_download_url": url, **plus}


class MiseAJour(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(se.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(se.parse_version("1.10.0"), (1, 10, 0))
        self.assertIsNone(se.parse_version("v1.2"))
        self.assertIsNone(se.parse_version("v1.2.3-beta"))
        self.assertIsNone(se.parse_version(""))

    def test_plus_recente(self):
        trouve = se.find_update(fiche("v1.10.0", [actif(size=42, digest="sha256:ABCD")]), "1.9.3")
        self.assertEqual(trouve["version"], "1.10.0")  # 1.10 > 1.9 : des nombres, pas du texte
        self.assertEqual(trouve["size"], 42)
        self.assertEqual(trouve["sha256"], "abcd")
        self.assertTrue(trouve["url"].startswith(se.URL_DOWNLOADS))

    def test_la_meme_ou_une_plus_ancienne_ne_se_propose_pas(self):
        self.assertIsNone(se.find_update(fiche("v1.1.0", [actif()]), "1.1.0"))
        self.assertIsNone(se.find_update(fiche("v1.0.0", [actif()]), "1.1.0"))

    def test_un_tag_qui_n_est_pas_une_version(self):
        self.assertIsNone(se.find_update(fiche("essai", [actif()]), "1.0.0"))
        self.assertIsNone(se.find_update({}, "1.0.0"))

    def test_sans_exe_rien_a_installer(self):
        self.assertIsNone(se.find_update(fiche("v2.0.0", []), "1.0.0"))
        self.assertIsNone(se.find_update(fiche("v2.0.0", [actif("notes.txt")]), "1.0.0"))

    def test_un_exe_venu_d_ailleurs_est_refuse(self):
        ailleurs = actif(url="https://exemple.org/Prisma.Safe.exe")
        self.assertIsNone(se.find_update(fiche("v2.0.0", [ailleurs]), "1.0.0"))

    def test_sans_empreinte_annoncee(self):
        self.assertIsNone(se.find_update(fiche("v2.0.0", [actif()]), "1.0.0")["sha256"])

    def test_la_version_du_script_se_lit(self):
        self.assertIsNotNone(se.parse_version(se.VERSION))


class Telechargement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.exe = Path(self._tmp.name) / "nouveau.exe"
        self.contenu = b"MZ" + b"\0" * 100
        self.exe.write_bytes(self.contenu)
        self.empreinte = hashlib.sha256(self.contenu).hexdigest()

    def test_bon_fichier(self):
        se.verify_download(self.exe, len(self.contenu), self.empreinte)
        se.verify_download(self.exe)  # GitHub n'a rien annoncé : on vérifie ce qu'on peut

    def test_tronque(self):
        with self.assertRaises(ValueError):
            se.verify_download(self.exe, len(self.contenu) + 1, self.empreinte)

    def test_altere(self):
        with self.assertRaises(ValueError):
            se.verify_download(self.exe, len(self.contenu), "0" * 64)

    def test_pas_un_exe(self):
        self.exe.write_bytes(b"<html>erreur</html>")
        with self.assertRaises(ValueError):
            se.verify_download(self.exe)


class Reglages(unittest.TestCase):
    def test_le_verrou_suit_le_dossier_de_reglages(self):
        self.assertTrue(str(se.CONFIG_DIR).startswith(_appdata.name))
        self.assertRegex(se.INSTANCE_ID, r"^PrismaSafe-[0-9a-f]{12}$")

    def test_les_reglages_de_sauvegarde_event_sont_repris_une_fois(self):
        self.addCleanup(lambda: se.CONFIG_FILE.unlink(missing_ok=True))
        se.CONFIG_FILE.unlink(missing_ok=True)
        se.adopt_old_config()  # PC neuf : rien à reprendre, rien n'est créé
        self.assertFalse(se.config_is_set())

        se.OLD_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (se.OLD_CONFIG_DIR / "config.json").write_text('{"dest": "D:\\\\TRI", "auto": false}',
                                                       encoding="utf-8")
        se.adopt_old_config()
        self.assertTrue(se.config_is_set())
        self.assertEqual(se.load_config()["dest"], r"D:\TRI")
        self.assertEqual(se.load_config()["src_hd"], se.DEFAULT_CONFIG["src_hd"])

        se.save_config({**se.load_config(), "dest": r"E:\TRI"})
        se.adopt_old_config()  # les réglages faits depuis ne sont pas écrasés
        self.assertEqual(se.load_config()["dest"], r"E:\TRI")


if __name__ == "__main__":
    sys.exit(unittest.main())
