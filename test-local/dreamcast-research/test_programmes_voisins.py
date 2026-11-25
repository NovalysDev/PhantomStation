"""Programmes voisins d'une archive AFS (apprentissage.programmes_voisins).

Un faux disque : un système de fichiers ISO minimal dans la zone haute densité,
avec une archive BOSS.AFS de trois entrées. Aucun fichier de jeu n'est utilisé.
"""
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apprentissage as learn

SECTEUR = 2048
BASE = 0x8C4ACA20
PROLOGUE = bytes.fromhex('e62fd62fc62f436e')  # mov.l r14,@-r15 ; ... : un début de fonction


def _enregistrement(nom: bytes, lba: int, taille: int, dossier: bool) -> bytes:
    n = 33 + len(nom)
    n += n % 2
    r = bytearray(n)
    r[0] = n
    r[2:6] = lba.to_bytes(4, 'little')
    r[10:14] = taille.to_bytes(4, 'little')
    r[25] = 2 if dossier else 0
    r[32] = len(nom)
    r[33:33 + len(nom)] = nom
    return bytes(r)


class FauxDisque:
    def __init__(self, entrees: list[bytes]):
        self.secteurs = {}
        racine, afs = 45020, 46000
        pvd = bytearray(SECTEUR)
        pvd[0] = 1
        pvd[1:6] = b'CD001'
        pvd[156:190] = _enregistrement(b'\0', racine, SECTEUR, True)
        self.secteurs[45016] = bytes(pvd)
        # Archive : en-tête sur un secteur, puis chaque entrée sur ses propres secteurs.
        tete = bytearray(b'AFS\0' + len(entrees).to_bytes(4, 'little'))
        corps, off = b'', SECTEUR
        for e in entrees:
            tete += off.to_bytes(4, 'little') + len(e).to_bytes(4, 'little')
            bourre = e + bytes(-len(e) % SECTEUR)
            corps += bourre
            off += len(bourre)
        archive = bytes(tete) + bytes(SECTEUR - len(tete)) + corps
        for i in range(0, len(archive), SECTEUR):
            self.secteurs[afs + i // SECTEUR] = archive[i:i + SECTEUR]
        dossier = (_enregistrement(b'\0', racine, SECTEUR, True) + _enregistrement(b'\1', racine, SECTEUR, True)
                   + _enregistrement(b'BOSS.AFS;1', afs, len(archive), False))
        self.secteurs[racine] = dossier + bytes(SECTEUR - len(dossier))

    def sector(self, lba):
        return self.secteurs.get(lba, bytes(SECTEUR))


def _bloc(data: bytes, cibles=None) -> dict:
    data = data + bytes(-len(data) % SECTEUR)
    cle = hashlib.sha1(BASE.to_bytes(4, 'little') + data).hexdigest()[:12]
    return {'base': BASE, 'data': data, 'file': f'bloc-{cle}.bin',
            'targets': {BASE} if cibles is None else cibles, 'disc': True}


class ProgrammesVoisins(unittest.TestCase):
    def setUp(self):
        self.boss1 = PROLOGUE + b'\x01' * 100
        self.boss2 = PROLOGUE + b'\x02' * 3000          # deux secteurs
        self.donnees = bytes(50)                          # pas du code
        self.disque = FauxDisque([self.boss1, self.boss2, self.donnees])

    def test_un_boss_appris_amene_les_autres(self):
        nouveaux = learn.programmes_voisins([_bloc(self.boss1)], self.disque)
        self.assertEqual(len(nouveaux), 1)
        attendu = _bloc(self.boss2)
        self.assertEqual(nouveaux[0]['data'], attendu['data'])
        self.assertEqual(nouveaux[0]['file'], attendu['file'])
        self.assertEqual(nouveaux[0]['targets'], {BASE})
        self.assertEqual(nouveaux[0]['base'], BASE)

    def test_rien_de_nouveau_si_tous_connus(self):
        self.assertEqual(learn.programmes_voisins([_bloc(self.boss1), _bloc(self.boss2)], self.disque), [])

    def test_bloc_hors_archive_ignore(self):
        autre = _bloc(PROLOGUE + b'\x03' * 100)
        self.assertEqual(learn.programmes_voisins([autre], self.disque), [])

    def test_bloc_sans_entree_au_debut_ignore(self):
        self.assertEqual(learn.programmes_voisins([_bloc(self.boss1, {BASE + 8})], self.disque), [])

    def test_bloc_de_memoire_ignore(self):
        bloc = _bloc(self.boss1)
        bloc['disc'] = False
        self.assertEqual(learn.programmes_voisins([bloc], self.disque), [])

    def test_disque_sans_iso(self):
        class Vide:
            def sector(self, lba): return bytes(SECTEUR)
        self.assertEqual(learn.programmes_voisins([_bloc(self.boss1)], Vide()), [])

    def test_learned_ajoute_et_compte(self):
        appris = learn.Learned(Path(__file__).resolve().parent / 'inexistant-programmes-voisins')
        appris.blocks = [_bloc(self.boss1)]
        self.assertEqual(appris.add_archive_siblings(self.disque), 1)
        self.assertEqual(len(appris.blocks), 2)
        self.assertEqual(appris.add_archive_siblings(self.disque), 0)


if __name__ == '__main__':
    unittest.main()
