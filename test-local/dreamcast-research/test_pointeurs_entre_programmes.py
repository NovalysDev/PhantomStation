"""Fonctions qu'un programme désigne dans un autre (apprentissage.pointeurs_entre_programmes).

De faux programmes SH-4 écrits à la main ; aucun fichier de jeu n'est utilisé.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apprentissage as learn

JEU = 0x8C42E800
BOSS = 0x8C4ACA20


def _code(*ops: int) -> bytes:
    return b''.join(op.to_bytes(2, 'little') for op in ops)


# Deux fonctions : f0 en +0, f1 en +20 (juste après RTS et son délai).
PROGRAMME_JEU = (_code(0x2FE6, 0x4F22, 0x7FFC, 0x6043,      # f0 : sauvegardes, pile agrandie
                       0x0009, 0x0009, 0x7F04, 0x4F26,      #      corps, fin
                       0x000B, 0x6EF6,                      #      RTS
                       0x2FE6, 0x2FD6, 0x4F22, 0x7FF8,      # f1 : +20
                       0x0009, 0x7F08, 0x4F26, 0x6DF6,      #      +28 : milieu, fin de f1
                       0x000B, 0x6EF6)                      #      RTS
                 + _code(*[0x0009] * 12))
F1 = JEU + 20
MILIEU = JEU + 28


def _mots(*valeurs: int) -> bytes:
    return b''.join(v.to_bytes(4, 'little') for v in valeurs)


def _bloc(base, data, nom, disc=True):
    return {'base': base, 'data': data, 'file': nom, 'targets': {base}, 'disc': disc}


class PointeursEntreProgrammes(unittest.TestCase):
    def setUp(self):
        self.jeu = _bloc(JEU, PROGRAMME_JEU, 'bloc-jeu.bin')
        # Le boss commence comme une fonction puis range ses adresses.
        self.boss = _bloc(BOSS, _code(0x2FE6, 0x4F22, 0x000B, 0x0009)
                          + _mots(F1, MILIEU, BOSS + 4, 0x12345678, F1 & 0x1FFFFFFF), 'bloc-boss.bin')

    def test_fonction_du_jeu_designee_par_le_boss(self):
        trouves = learn.pointeurs_entre_programmes([self.jeu, self.boss])
        self.assertEqual(trouves, {'bloc-jeu.bin': {F1}})

    def test_milieu_de_fonction_refuse(self):
        trouves = learn.pointeurs_entre_programmes([self.jeu, self.boss])
        self.assertNotIn(MILIEU, trouves.get('bloc-jeu.bin', set()))

    def test_programme_charge_a_la_meme_adresse_ignore(self):
        # Un autre boss chargé au même endroit remplace celui-ci : jamais ensemble.
        autre = _bloc(BOSS, PROGRAMME_JEU, 'bloc-autre-boss.bin')
        pointeur = _bloc(BOSS, _code(0x2FE6, 0x4F22, 0x000B, 0x0009) + _mots(BOSS + 20), 'bloc-boss2.bin')
        self.assertEqual(learn.pointeurs_entre_programmes([autre, pointeur]), {})

    def test_bloc_de_memoire_ignore(self):
        self.boss['disc'] = False
        self.assertEqual(learn.pointeurs_entre_programmes([self.jeu, self.boss]), {})

    def test_prologue_strict(self):
        self.assertTrue(learn._prologue(PROGRAMME_JEU, 20))
        self.assertFalse(learn._prologue(PROGRAMME_JEU, 28))
        self.assertFalse(learn._prologue(PROGRAMME_JEU, len(PROGRAMME_JEU) - 2))

    def test_learned_ajoute_une_fois(self):
        appris = learn.Learned(Path(__file__).resolve().parent / 'inexistant-pointeurs')
        appris.blocks = [self.jeu, self.boss]
        self.assertEqual(appris.add_cross_pointers(), 1)
        self.assertIn(F1, self.jeu['targets'])
        self.assertEqual(appris.add_cross_pointers(), 0)


if __name__ == '__main__':
    unittest.main()
