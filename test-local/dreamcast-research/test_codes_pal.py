"""Codes de triche PAL : déchiffrement CDX, fichiers .cht, choix du fichier
d'après le titre, et codes à plusieurs écritures dans le menu F11 du moteur.

Les mots chiffrés ci-dessous sont quelques codes publics de Grandia II PAL et
de Crazy Taxi PAL (bucanero.com.ar) ; aucune donnée de jeu n'est utilisée.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT))
import codes_cdx  # noqa: E402


class Dechiffrement(unittest.TestCase):
    def test_exemple_publie(self):
        self.assertEqual(codes_cdx.dechiffre_mot(0x154C1517)[0], 0x01162472)

    def test_grandia_pal_comme_le_fichier_europeen(self):
        # Mêmes adresses que cheats/grandia_ii_europe.cht, écrit à la main.
        self.assertEqual(codes_cdx.ecritures(codes_cdx.mots_du_texte('E1F7E62C 05F5E0FF')), [(14867684, 99999999, 4)])
        self.assertEqual(codes_cdx.ecritures(codes_cdx.mots_du_texte('21679DFD 05F5E0FF')), [(14867688, 99999999, 4)])
        # Deux lignes : chaque ligne repart de l'état initial (vie de Ryudo en deux moitiés).
        self.assertEqual(codes_cdx.ecritures(codes_cdx.mots_du_texte('FF79A831 0000270F 30A91AF0 0000270F')),
                         [(14869372, 9999, 2), (14869374, 9999, 2)])

    def test_codes_ecartes(self):
        mots = codes_cdx.mots_du_texte
        self.assertIsNone(codes_cdx.ecritures(mots('4CBAC3F4 1456DC2F')))      # valeur chiffrée
        self.assertIsNone(codes_cdx.ecritures(mots('71D5DFAE 000F423F')))      # 16 bits trop grand
        self.assertIsNone(codes_cdx.ecritures(mots('EC8DB760 143C2DB4')))      # code d'activation
        self.assertIsNone(mots('07B67FA 00000001'))                             # mot incomplet
        self.assertEqual(mots('AC0350270000173E'), [0xAC035027, 0x173E])       # mots collés
        self.assertEqual(codes_cdx.ecritures([0x015228EC, 0xFF], chiffre=False), [(0x5228EC, 0xFF, 2)])

    def test_fichier_cht(self):
        texte = codes_cdx.cht([('Vie “infinie”', [(100, 9, 2), (102, 9, 2)]),
                               ('Vie “infinie”', [(100, 9, 2), (102, 9, 2)]),   # doublon exact
                               ('Vie “infinie”', [(200, 1, 1)])], 'Essai')
        self.assertTrue(texte.startswith('# Essai\ncheats = 3\n'))
        self.assertEqual(texte.count('_desc = "Vie \'infinie\'"'), 2)
        self.assertIn('cheat2_desc = "Vie \'infinie\' 2"', texte)
        self.assertIn('cheat2_memory_search_size = "3"', texte)


class ChoixDuFichier(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('pd_codes', ROOT / 'phantom_dreamcast.py')
        self.d = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.d)
        self.tmp = tempfile.TemporaryDirectory()
        self.d.CODES_PAL = Path(self.tmp.name)
        for nom in ('Grandia 2', 'House of the Dead 2', 'Rayman 2 The Great Escape', 'Re-volt',
                    'Sonic Adventures', 'Resident Evil 3 Nemesis', 'V-Rally 2'):
            (self.d.CODES_PAL / (nom + ' (Europe).cht')).write_text('cheats = 0\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def choisi(self, titre):
        f = self.d.codes_pal(titre)
        return f.stem if f else None

    def test_titres_de_la_bibliotheque(self):
        self.assertEqual(self.choisi('Grandia II'), 'Grandia 2 (Europe)')
        self.assertEqual(self.choisi('The House of the Dead 2'), 'House of the Dead 2 (Europe)')
        self.assertEqual(self.choisi('Rayman 2 - The Great Escape'), 'Rayman 2 The Great Escape (Europe)')
        self.assertEqual(self.choisi('Re-Volt (Europe) (En,Fr,De,Es,It)'), 'Re-volt (Europe)')
        self.assertEqual(self.choisi('Sonic Adventure'), 'Sonic Adventures (Europe)')
        self.assertEqual(self.choisi('Resident Evil 3 - Nemesis'), 'Resident Evil 3 Nemesis (Europe)')

    def test_pas_de_faux_choix(self):
        self.assertIsNone(self.choisi('Grandia II (USA)'))
        self.assertIsNone(self.choisi('Grandia'))
        self.assertIsNone(self.choisi('Jeu inconnu'))
        self.assertEqual(self.d.codes_normalise('V-Rally 2'), 'v rally 2')   # « V » n'est pas un chiffre romain ici


GROUPES_C = r'''#include "hal/dc_cheats.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint8_t ram[16 * 1024 * 1024];
uint8_t *sh4_get_ram_ptr(void) { return ram; }
uint32_t sh4_get_ram_size(void) { return sizeof ram; }
uint32_t sh4_get_ram_mask(void) { return sizeof ram - 1; }
int main(void) {
    dc_cheats_tick(); dc_cheats_menu_toggle();
    const char *t = dc_cheats_menu_text();
    /* « Vie » (deux lignes) et « Argent » ; « Casse » a une ligne refusée. */
    assert(strstr(t, "Vie") && strstr(t, "Argent") && !strstr(t, "Casse") && strstr(t, "1/2"));
    dc_cheats_menu_select();
    for (int i = 0; i < 40; ++i) dc_cheats_tick();
    assert(ram[100] == 0x0F && ram[101] == 0x27 && ram[102] == 0x0F && ram[103] == 0x27);
    assert(ram[300] == 0 && ram[400] == 0);
    puts("groupes OK");
    return 0;
}
'''

GROUPES_CHT = '''cheats = 5
cheat0_desc = "Vie"
cheat0_address = "100"
cheat0_value = "9999"
cheat0_memory_search_size = "4"
cheat0_cheat_type = "1"
cheat0_handler = "1"
cheat1_desc = "Vie"
cheat1_address = "102"
cheat1_value = "9999"
cheat1_memory_search_size = "4"
cheat1_cheat_type = "1"
cheat1_handler = "1"
cheat2_desc = "Argent"
cheat2_address = "300"
cheat2_value = "7"
cheat2_memory_search_size = "3"
cheat2_cheat_type = "1"
cheat2_handler = "1"
cheat3_desc = "Casse"
cheat3_address = "400"
cheat3_value = "1"
cheat3_memory_search_size = "3"
cheat3_cheat_type = "1"
cheat3_handler = "1"
cheat4_desc = "Casse"
cheat4_address = "401"
cheat4_value = "1"
cheat4_memory_search_size = "3"
cheat4_cheat_type = "2"
cheat4_handler = "1"
'''


class MenuDuMoteur(unittest.TestCase):
    def test_code_a_plusieurs_ecritures(self):
        with tempfile.TemporaryDirectory(prefix='phantom-codes-') as tmp:
            tmp = Path(tmp)
            (tmp / 'essai.c').write_text(GROUPES_C, encoding='utf-8')
            (tmp / 'phantom-cheats.cht').write_text(GROUPES_CHT, encoding='utf-8')
            env = os.environ.copy()
            env['PATH'] = str(ROOT / 'toolchain/bin') + os.pathsep + env['PATH']
            env['PHANTOM_DC_CHEATS_DELAI'] = '0'
            env.pop('PHANTOM_DC_CHEATS', None)
            subprocess.run([str(ROOT / 'toolchain/bin/clang.exe'), '-std=c11', '-O1', '-w', '-I', str(LAB / 'dcrecomp/include'),
                            str(tmp / 'essai.c'), str(LAB / 'dcrecomp/src/hal/dc_cheats.c'), '-o', str(tmp / 'essai.exe')],
                           env=env, check=True, capture_output=True, text=True)
            fini = subprocess.run([str(tmp / 'essai.exe')], cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)
            self.assertIn('Vie : actif (2 ecriture(s))', fini.stdout)


if __name__ == '__main__':
    unittest.main()
