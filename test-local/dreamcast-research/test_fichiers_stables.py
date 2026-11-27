"""Compilation plus rapide pendant l'apprentissage (--stable-files).

Entre deux essais du jeu, seuls les fichiers C dont le contenu a changé sont
recompilés (apprentissage.sync_tree garde les autres intacts). Il faut donc :
  * des fichiers découpés à des frontières qui ne dépendent que des adresses
    voisines : une fonction ajoutée ne décale pas tous les fichiers suivants ;
  * des déclarations propres à chaque fichier, sans l'en-tête commun
    game_functions.h qui change à chaque nouvelle fonction ;
  * des fichiers assez petits pour occuper tous les cœurs.
Cas réel : South Park Rally (26/11/2026), 6 compilations complètes en 28 min.
"""
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

LAB = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sr_stable', LAB / 'dcrecomp/tools/static_recompile.py')
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)
sys.path.insert(0, str(LAB))
import apprentissage as learn  # noqa: E402

BASE = 0x8C010000


def fonction(addr, appels=(), next_func_addr=None):
    corps = ''.join(f'    func_{c:08X}(cpu);\n' for c in appels)
    suite = f'    func_{next_func_addr:08X}(cpu); return; /* fall-through */\n' if next_func_addr else ''
    return f'void func_{addr:08X}(SH4CPU *cpu) {{\n{corps}{suite}}}\n'


def traducteur(adresses, appels=None, geantes=()):
    rec = sr.SH4Recompiler.__new__(sr.SH4Recompiler)
    rec.functions = {a: None for a in adresses}
    appels = appels or {}

    def recompile_function(addr, info, next_func_addr=None):
        code = fonction(addr, appels.get(addr, ()), next_func_addr)
        if addr in geantes:
            code += '/* ' + 'x' * (sr.SH4Recompiler.GIANT_FUNCTION_CHARS + 10) + ' */\n'
        return code
    rec.recompile_function = recompile_function
    return rec


def generer(rec, dossier):
    sr.STABLE_FILES = True
    try:
        rec.recompile_all(str(dossier))
    finally:
        sr.STABLE_FILES = False
    return {p.name: p.read_text(encoding='utf-8') for p in Path(dossier).glob('game_code_*.c')}


class FichiersStables(unittest.TestCase):
    def setUp(self):
        self.adresses = [BASE + 0x40 * i for i in range(3000)]

    def test_une_fonction_ajoutee_ne_change_que_son_fichier(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            avant = generer(traducteur(self.adresses), a)
            nouvelle = BASE + 0x40 * 1500 + 0x20                      # au milieu du programme
            apres = generer(traducteur(sorted(self.adresses + [nouvelle])), b)
            changes = [n for n in apres if avant.get(n) != apres[n]]
            # Le fichier de la nouvelle fonction ; au plus aussi le voisin
            # quand elle devient elle-même une frontière.
            self.assertLessEqual(len(changes), 2, changes)
            self.assertTrue(any(f'func_{nouvelle:08X}(SH4CPU *cpu) {{' in apres[n] for n in changes))
            self.assertGreater(len(avant), 10)                        # assez de fichiers pour 15 cœurs

    def test_fichiers_de_taille_bornee(self):
        with tempfile.TemporaryDirectory() as a:
            fichiers = generer(traducteur(self.adresses), a)
            tailles = [len(re.findall(r'^void func_[0-9A-F]{8}\(SH4CPU \*cpu\) \{', t, re.M)) for t in fichiers.values()]
            self.assertEqual(sum(tailles), len(self.adresses))
            self.assertLessEqual(max(tailles), sr.SH4Recompiler.STABLE_MAX_FUNCTIONS)

    def test_declarations_propres_au_fichier(self):
        appels = {BASE: [0x8C200000, BASE + 0x40]}
        with tempfile.TemporaryDirectory() as a:
            fichiers = generer(traducteur(self.adresses[:50], appels), a)
            for texte in fichiers.values():
                self.assertNotIn('game/game_functions.h', texte)
                for nom in set(re.findall(r'\bfunc_[0-9A-F]{8}\b', texte)):
                    self.assertIn(f'void {nom}(SH4CPU *cpu);\n', texte)
            premier = next(t for t in fichiers.values() if f'void func_{BASE:08X}(SH4CPU *cpu) {{' in t)
            self.assertIn('void func_8C200000(SH4CPU *cpu);\n', premier)
            self.assertIn('void sh4_jump_indirect(SH4CPU *cpu);\n', premier)

    def test_fonction_geante_a_part(self):
        geante = self.adresses[10]
        with tempfile.TemporaryDirectory() as a:
            fichiers = generer(traducteur(self.adresses[:40], geantes={geante}), a)
            nom = f'game_code_lourd_{geante:08X}.c'
            self.assertIn(nom, fichiers)
            self.assertEqual(Path(a, 'compilation-legere.txt').read_text().split(), [nom])
            self.assertNotIn('game/game_functions.h', fichiers[nom])
            self.assertIn('static uint32_t phantom_entree_lourde;', fichiers[nom])

    def test_mode_classique_inchange(self):
        with tempfile.TemporaryDirectory() as a:
            rec = traducteur(self.adresses[:600])
            rec.recompile_all(a)
            self.assertEqual(sorted(os.listdir(a)), ['compilation-legere.txt', 'game_code_000.c', 'game_code_001.c'])
            self.assertIn('#include "game/game_functions.h"', Path(a, 'game_code_000.c').read_text())


class BlocsAppris(unittest.TestCase):
    def test_noms_stables_et_fonction_geante_legere(self):
        """Un bloc appris garde le nom de ses fichiers d'un essai à l'autre, et
        sa fonction géante reste compilée sans optimisation."""
        bloc = {'base': 0x8C800000, 'data': bytes(0x4000), 'targets': [0x8C800000], 'file': 'bloc-0123456789ab.bin'}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stage = tmp / 'etape'; (stage / 'src/game').mkdir(parents=True); (stage / 'include/game').mkdir(parents=True)

            def recompile(binary, src, inc, base, entries):
                adresses = [0x8C800000 + 0x40 * i for i in range(300)]
                Path(inc).mkdir(parents=True, exist_ok=True)
                Path(inc, 'game_functions.h').write_text(''.join(f'void func_{a:08X}(SH4CPU *cpu);\n' for a in adresses))
                generer(traducteur(adresses, geantes={0x8C800040}), src)
            learn.translate_block(bloc, tmp, recompile, stage, 'essai')
            noms = sorted(p.name for p in (stage / 'src/game').glob('game_code_L*.c'))
            cle = learn._key(bloc)
            self.assertIn(f'game_code_L{cle}_lourd_8C800040.c', noms)
            self.assertTrue(all(re.fullmatch(rf'game_code_L{cle}_(8C[0-9A-F]{{6}}|fin|lourd_8C[0-9A-F]{{6}})\.c', n) for n in noms), noms)
            legers = (stage / 'src/game/compilation-legere.txt').read_text().split()
            self.assertIn(f'game_code_L{cle}_lourd_8C800040.c', legers)
            texte = (stage / 'src/game' / f'game_code_L{cle}_fin.c').read_text()
            self.assertIn(f'void L{cle}_func_', texte)                  # fonctions du bloc renommées


class AssemblageProfils(unittest.TestCase):
    def test_numero_suivant_ignore_les_noms_non_numeriques(self):
        spec = importlib.util.spec_from_file_location('assemble', LAB / 'pal-prototype/assemble_disjoint_overlays.py')
        texte = Path(spec.origin).read_text(encoding='utf-8')
        self.assertIn('isdigit()', texte)


if __name__ == '__main__':
    unittest.main()
