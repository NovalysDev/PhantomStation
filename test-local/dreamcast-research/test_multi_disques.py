"""Jeux Dreamcast sur plusieurs disques : le changement de disque (F4) passe
par un plateau ouvert un instant, puis le disque suivant est en place.

Le test du lecteur CHD utilise deux disques de la bibliothèque du créateur
s'ils sont présents ; il est sauté sinon (aucune donnée de jeu n'est publiée).
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
CLANG = ROOT / 'toolchain/bin/clang.exe'

ETATS_C = r'''#include "hal/dc_disques.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static uint64_t maintenant;
uint64_t platform_get_ticks_us(void) { return maintenant; }
static int demandes[8], nb, refuse = -1;
static int changer(int i) { demandes[nb++] = i; return i == refuse ? -1 : 0; }
int main(void) {
    assert(dc_disques_etat_lecteur() == 2 && dc_disques_message() == NULL);
    dc_disques_suivant(); maintenant = 1;                       /* un seul disque */
    assert(strstr(dc_disques_message(), "QU'UN DISQUE") && dc_disques_etat_lecteur() == 2);
    dc_disques_configure(3, changer);
    maintenant = 10000000; dc_disques_suivant();
    assert(dc_disques_etat_lecteur() == 6);                      /* plateau ouvert */
    assert(strstr(dc_disques_message(), "DISQUE 2/3"));
    maintenant += 1000000; assert(dc_disques_etat_lecteur() == 6 && nb == 0);
    maintenant += 600000;  assert(dc_disques_etat_lecteur() == 2); /* refermé, disque 2 */
    assert(nb == 1 && demandes[0] == 1 && dc_disques_courant() == 1);
    dc_disques_suivant(); dc_disques_suivant();                  /* deux appuis : 2 -> 3 -> 1 */
    maintenant += 2000000; assert(dc_disques_etat_lecteur() == 2);
    assert(nb == 2 && demandes[1] == 0 && dc_disques_courant() == 0);   /* un seul changement : disque 1 */
    refuse = 1; dc_disques_suivant(); maintenant += 2000000;
    assert(dc_disques_etat_lecteur() == 2 && dc_disques_courant() == 0);  /* disque 2 illisible : le 1 reste */
    assert(strstr(dc_disques_message(), "ILLISIBLE"));
    puts("etats OK");
    return 0;
}
'''

CHD_C = r'''#include <stdio.h>
#include <string.h>
int pal_chd_open_stream(FILE *stream);
int pal_chd_switch_stream(FILE *stream);
int pal_chd_sector(unsigned lba, unsigned char out[2048]);
/* IP.BIN, au début de la zone haute densité : « GD-ROM1/2 », « GD-ROM2/2 ». */
static void numero(char *out) {
    unsigned char s[2048] = {0};
    if (pal_chd_sector(45000, s)) { strcpy(out, "?"); return; }
    memcpy(out, s + 0x2B, 5); out[5] = 0;
}
int main(int argc, char **argv) {
    FILE *a = fopen(argv[1], "rb"), *b = fopen(argv[2], "rb");
    char n[8];
    if (!a || !b || pal_chd_open_stream(a)) return 2;
    numero(n); printf("avant %s\n", n);
    if (pal_chd_switch_stream(b)) return 3;
    numero(n); printf("apres %s\n", n);
    if (pal_chd_switch_stream(a)) return 4;
    numero(n); printf("retour %s\n", n);
    return 0;
}
'''


def compiler(source, fichiers, tmp, lien=()):
    env = os.environ.copy()
    env['PATH'] = str(ROOT / 'toolchain/bin') + os.pathsep + env['PATH']
    (tmp / 'essai.c').write_text(source, encoding='utf-8')
    subprocess.run([str(CLANG), '-std=c11', '-O1', '-w', '-I', str(LAB / 'dcrecomp/include'),
                    '-I', str(next(LAB.glob('libchdr-*')) / 'include'), str(tmp / 'essai.c'), *map(str, fichiers),
                    *lien, '-o', str(tmp / 'essai.exe')], env=env, check=True, capture_output=True, text=True)
    return env


class ChangementDeDisque(unittest.TestCase):
    def test_plateau_et_disque_suivant(self):
        with tempfile.TemporaryDirectory(prefix='phantom-disques-') as tmp:
            tmp = Path(tmp)
            env = compiler(ETATS_C, [LAB / 'dcrecomp/src/hal/dc_disques.c'], tmp)
            fini = subprocess.run([str(tmp / 'essai.exe')], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)

    def test_lecteur_chd_change_de_disque(self):
        # Dossier contenant les deux disques d'Alone in the Dark (France) en .chd.
        dossier = os.environ.get('PHANTOM_DISQUES_ESSAI', '')
        disques = sorted(Path(dossier).glob('Alone in the Dark - The New Nightmare (France) (Disc *).chd')) if dossier else []
        if len(disques) != 2:
            self.skipTest('disques d’essai absents (PHANTOM_DISQUES_ESSAI)')
        with tempfile.TemporaryDirectory(prefix='phantom-chd-') as tmp:
            tmp = Path(tmp)
            env = compiler(CHD_C, [LAB / 'pal-prototype/pal_chd.c'], tmp, [str(LAB / 'libchdr-build/libchdr.dll.a')])
            env['PATH'] = str(LAB / 'libchdr-build') + os.pathsep + env['PATH']
            fini = subprocess.run([str(tmp / 'essai.exe'), *map(str, disques)], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)
            self.assertEqual(fini.stdout.split(), ['avant', '1/2', 'apres', '2/2', 'retour', '1/2'])


if __name__ == '__main__':
    unittest.main()
