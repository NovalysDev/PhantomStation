"""Nombre de cœurs des compilations : jamais tous, réglage enregistré, et
utilisé par chaque console."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'test-local' / 'dreamcast-research'))
import coeurs  # noqa: E402


class Coeurs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reglages = patch.object(coeurs, 'REGLAGES', Path(self.tmp.name) / 'compilateur.json')
        self.reglages.start()
        self.env = patch.dict(os.environ, {'PHANTOM_JOBS': '', 'PHANTOM_DC_JOBS': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.reglages.stop(); self.tmp.cleanup()

    def test_reserve_windows_toujours_gardee(self):
        # (threads, Go, automatique, maximum, réserve de Windows)
        for cpu, memoire, auto, maxi, reserve in ((20, 32, 15, 16, 4), (8, 16, 6, 6, 2), (4, 8, 2, 2, 2),
                                                  (2, 8, 1, 1, 1), (1, 8, 1, 1, 0), (32, 16, 8, 8, 6),
                                                  (16, 64, 12, 13, 3), (20, None, 15, 16, 4)):
            with patch.object(coeurs, 'processeur', return_value=cpu), patch.object(coeurs, 'memoire_go', return_value=memoire):
                self.assertEqual((coeurs.recommande(), coeurs.maximum(), coeurs.reserve_windows()), (auto, maxi, reserve), (cpu, memoire))
                self.assertLessEqual(coeurs.recommande(), coeurs.maximum())
                if cpu > 1:
                    self.assertLessEqual(coeurs.maximum(), cpu - coeurs.reserve_windows())
                    self.assertGreaterEqual(coeurs.reserve_windows(), 1)

    def test_choix_enregistre_et_borne(self):
        with patch.object(coeurs, 'processeur', return_value=20), patch.object(coeurs, 'memoire_go', return_value=32):
            self.assertEqual(coeurs.choix(), 'auto')
            self.assertEqual(coeurs.nombre(), 15)
            coeurs.enregistrer(12)
            self.assertEqual(coeurs.choix(), 12)
            self.assertEqual(coeurs.nombre(), 12)
            coeurs.enregistrer(64)                       # borné à 16 : 4 threads restent à Windows
            self.assertEqual(coeurs.choix(), 16)
            self.assertEqual(coeurs.nombre(), 16)
            coeurs.enregistrer('auto')
            self.assertEqual(coeurs.nombre(), 15)
            # Les autres réglages du fichier sont gardés.
            data = json.loads(coeurs.REGLAGES.read_text(encoding='utf-8'))
            data['dossier_jeux'] = 'C:/Jeux'; coeurs.REGLAGES.write_text(json.dumps(data), encoding='utf-8')
            coeurs.enregistrer(8)
            self.assertEqual(json.loads(coeurs.REGLAGES.read_text(encoding='utf-8')), {'coeurs': 8, 'dossier_jeux': 'C:/Jeux'})
            with patch.dict(os.environ, {'PHANTOM_JOBS': '3'}):
                self.assertEqual(coeurs.nombre(), 3)

    def test_dreamcast_suit_le_reglage(self):
        import build_pal_prototype
        with patch.object(coeurs, 'processeur', return_value=20), patch.object(coeurs, 'memoire_go', return_value=32):
            coeurs.enregistrer(7)
            self.assertEqual(build_pal_prototype.compile_jobs(), 7)


if __name__ == '__main__':
    unittest.main()
