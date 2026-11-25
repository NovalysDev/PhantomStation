"""L'apprentissage voyage avec le jeu compilé, et le moteur d'un jeu est daté.

Faux jeux compilés (archives zip) écrits dans un dossier temporaire ; aucun
fichier de jeu n'est utilisé.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(LAB.parents[1]))
import apprentissage as learn

DISQUE = 'a' * 40
BASE = 0x8C4ACA20
OCTETS = bytes.fromhex('e62f224f') * 512


def _nom(base, octets):
    return f"bloc-{hashlib.sha1(base.to_bytes(4, 'little') + octets).hexdigest()[:12]}.bin"


def _paquet(dossier, disque=DISQUE, octets=OCTETS, cibles=(BASE,), principal=('0x8C02385E',), moteur='b' * 64):
    chemin = Path(dossier) / ('jeu-' + os.urandom(4).hex() + '.exe')
    nom = _nom(BASE, OCTETS)
    profil = {'principal': list(principal),
              'blocs': [{'base': f'0x{BASE:08X}', 'taille': len(octets), 'fichier': nom, 'source': 'disque',
                         'cibles': [f'0x{c:08X}' for c in cibles]}]}
    with zipfile.ZipFile(chemin, 'w') as z:
        z.writestr('apprentissage/disque.txt', disque)
        z.writestr('apprentissage/profil.json', json.dumps(profil))
        z.writestr('apprentissage/' + nom, octets)
        if moteur is not None: z.writestr('moteur.txt', moteur)
    return chemin


class ApprentissageVoyage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.appris = learn.Learned(Path(self.tmp.name) / 'vide')

    def tearDown(self):
        self.tmp.cleanup()

    def test_reprise_puis_aucun_doublon(self):
        paquet = _paquet(self.tmp.name)
        self.assertEqual(learn.fusionner_paquet(self.appris, paquet, DISQUE), 2)
        self.assertEqual(len(self.appris.blocks), 1)
        self.assertEqual(self.appris.blocks[0]['targets'], {BASE})
        self.assertIn(0x8C02385E, self.appris.main)
        self.assertEqual(learn.fusionner_paquet(self.appris, paquet, DISQUE), 0)

    def test_cibles_ajoutees_sans_rien_retirer(self):
        learn.fusionner_paquet(self.appris, _paquet(self.tmp.name), DISQUE)
        plus = _paquet(self.tmp.name, cibles=(BASE, BASE + 0x40))
        self.assertEqual(learn.fusionner_paquet(self.appris, plus, DISQUE), 1)
        self.assertEqual(self.appris.blocks[0]['targets'], {BASE, BASE + 0x40})

    def test_autre_disque_ignore(self):
        self.assertEqual(learn.fusionner_paquet(self.appris, _paquet(self.tmp.name), 'c' * 40), 0)
        self.assertEqual(self.appris.blocks, [])

    def test_bloc_altere_refuse(self):
        self.assertEqual(learn.fusionner_paquet(self.appris, _paquet(self.tmp.name, octets=OCTETS[:-1] + b'\0'), DISQUE), 1)
        self.assertEqual(self.appris.blocks, [])  # seule l'entrée du programme principal est reprise

    def test_jeu_sans_apprentissage(self):
        chemin = Path(self.tmp.name) / 'ancien.exe'
        with zipfile.ZipFile(chemin, 'w') as z: z.writestr('game.toml', '')
        self.assertEqual(learn.fusionner_paquet(self.appris, chemin, DISQUE), 0)

    def test_fichiers_a_ranger(self):
        ancien = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = self.tmp.name
        try:
            self.assertEqual(learn.fichiers_a_ranger(DISQUE), [])
            appris = learn.Learned(learn.cache_root(DISQUE))
            learn.fusionner_paquet(appris, _paquet(self.tmp.name), DISQUE)
            appris.save()
            ranges = [nom for _, nom in learn.fichiers_a_ranger(DISQUE)]
            self.assertEqual(ranges, ['apprentissage/profil.json', 'apprentissage/' + _nom(BASE, OCTETS)])
        finally:
            if ancien is None: os.environ.pop('LOCALAPPDATA', None)
            else: os.environ['LOCALAPPDATA'] = ancien


class EmpreinteMoteur(unittest.TestCase):
    def test_stable(self):
        import build_pal_prototype
        a, b = build_pal_prototype.moteur_empreinte(), build_pal_prototype.moteur_empreinte()
        self.assertEqual(a, b)
        self.assertRegex(a, r'^[0-9a-f]{64}$')

    def test_moteur_du_jeu(self):
        import phantom_dc_autolearn as al
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache'; cache.mkdir()
            (cache / 'dreamcast.exe').write_bytes(b'moteur')
            self.assertEqual(al._moteur_du_jeu({'path': _paquet(tmp, moteur='d' * 64)}, cache), 'd' * 64)
            self.assertIsNone(al._moteur_du_jeu({'path': _paquet(tmp, moteur=None)}, cache))


if __name__ == '__main__':
    unittest.main()
