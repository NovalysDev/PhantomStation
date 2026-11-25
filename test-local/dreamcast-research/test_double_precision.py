"""Calcul flottant en double précision (FPSCR.PR = 1) dans le code traduit.

Le programme SH-4 est écrit à la main : c'est la division entière de la
bibliothèque de GCC pour le SH-4 (__udivsi3_i4), qui passe le processeur en
double, divise, puis remet FPSCR. Aucun fichier de jeu n'est utilisé.

Traduite toujours en simple précision, elle rendait 0 : Re-Volt divisait ainsi
la fréquence de son horloge, plafonnait chaque image à 0 et restait sur un
écran noir.
"""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
spec = importlib.util.spec_from_file_location('sh4_recomp_double', LAB / 'dcrecomp/tools/static_recompile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BASE = 0x8C010000

# __udivsi3_i4 : r4 / r5, quotient dans FPUL.
UDIVSI3_I4 = [
    0xE101,          # mov #1, r1
    0x3516,          # cmp/hi r1, r5
    0x8B11,          # bf trivial (diviseur 0 ou 1)
    0x4F62,          # sts.l fpscr, @-r15
    0xC70A,          # mova constantes, r0
    0x4066,          # lds.l @r0+, fpscr        (PR = 1)
    0x4105,          # rotr r1                  (r1 = 0x80000000)
    0x241A,          # xor r1, r4
    0x445A,          # lds r4, fpul
    0xF509,          # fmov.s @r0+, fr5         (moitié basse de 2^31)
    0xF408,          # fmov.s @r0, fr4          (moitié haute)
    0xF02D,          # float fpul, dr0
    0x251A,          # xor r1, r5
    0x455A,          # lds r5, fpul
    0xF22D,          # float fpul, dr2
    0xF040,          # fadd dr4, dr0
    0xF240,          # fadd dr4, dr2
    0xF023,          # fdiv dr2, dr0
    0xF03D,          # ftrc dr0, fpul
    0x000B,          # rts
    0x4F66,          # lds.l @r15+, fpscr
    0x000B,          # trivial: rts
    0x445A,          # lds r4, fpul
    0x0009,          # nop
    0x0000, 0x0000,  # bourrage jusqu'aux constantes (alignées sur 4)
    0x0000, 0x0008,  # FPSCR = 0x00080000 : PR
    0x0000, 0x0000,  # 2^31 en double : moitié basse...
    0x0000, 0x41E0,  # ...puis moitié haute
]

TEMPLATE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "recompiler/sh4_cpu.h"
uint32_t sh4_reloc;
static uint8_t code[256], pile[64];
static uint8_t *place(uint32_t a) {
    if (a >= 0x8C010000u && a < 0x8C010000u + sizeof code) return code + (a - 0x8C010000u);
    if (a >= 0x8C00F000u && a < 0x8C00F000u + sizeof pile) return pile + (a - 0x8C00F000u);
    printf("adresse inattendue %08X\n", a); exit(3);
}
uint32_t sh4_read32(SH4CPU *cpu, uint32_t a) { uint32_t v; memcpy(&v, place(a), 4); return v; }
void sh4_write32(SH4CPU *cpu, uint32_t a, uint32_t v) { memcpy(place(a), &v, 4); }
void sh4_fmov_load(SH4CPU *cpu, int n, uint32_t a) { memcpy(&cpu->fr[n], place(a), 4); }
void sh4_fmov_load_inc(SH4CPU *cpu, int n, int m) { sh4_fmov_load(cpu, n, cpu->r[m]); cpu->r[m] += 4; }
'''


def traduire(mots):
    binaire = struct.pack('<' + 'H' * len(mots), *mots)
    compilateur = module.SH4Recompiler(binaire, BASE)
    return binaire, compilateur.recompile_function(BASE, {'end': BASE + 2 * 24})


def executer(c, corps):
    with tempfile.TemporaryDirectory(prefix='phantom-double-') as tmp:
        dossier = Path(tmp)
        (dossier / 'essai.c').write_text(c + '\nint main(void) {\n' + corps + '\n}\n', encoding='utf-8')
        env = os.environ.copy()
        env['PATH'] = str(ROOT / 'toolchain/bin') + os.pathsep + env['PATH']
        subprocess.run([str(ROOT / 'toolchain/bin/clang.exe'), '-std=c11', '-O1',
                        '-Werror=implicit-function-declaration', '-I', str(LAB / 'dcrecomp/include'),
                        str(dossier / 'essai.c'), '-o', str(dossier / 'essai.exe')],
                       env=env, check=True, capture_output=True, text=True)
        return subprocess.run([str(dossier / 'essai.exe')], env=env, capture_output=True, text=True)


class DoublePrecision(unittest.TestCase):
    def test_instructions_regardent_pr(self):
        compilateur = module.SH4Recompiler(b'\0\0', BASE)
        for op in (0xF040, 0xF041, 0xF042, 0xF023, 0xF024, 0xF025, 0xF02D, 0xF03D, 0xF06D):
            texte = compilateur.recompile_instruction(op, BASE)[0]
            self.assertIn('SH4_PR(cpu)', texte, hex(op))
        self.assertIn('fcnvsd', compilateur.recompile_instruction(0xF2AD, BASE)[0])
        self.assertIn('fcnvds', compilateur.recompile_instruction(0xF2BD, BASE)[0])

    def test_division_de_gcc(self):
        binaire, traduit = traduire(UDIVSI3_I4)
        c = TEMPLATE + traduit.replace('void func_', 'void func_', 1)
        tableau = ', '.join(str(b) for b in binaire)
        corps = f'''
    static const uint8_t binaire[] = {{ {tableau} }};
    memcpy(code, binaire, sizeof binaire);
    static const uint32_t cas[][2] = {{ {{781250u, 10u}}, {{100u, 7u}}, {{0xFFFFFFFFu, 3u}}, {{5u, 9u}} }};
    for (int i = 0; i < 4; i++) {{
        SH4CPU cpu; memset(&cpu, 0, sizeof cpu);
        cpu.r[15] = 0x8C00F000u + sizeof pile;
        cpu.r[4] = cas[i][0]; cpu.r[5] = cas[i][1];
        func_{BASE:08X}(&cpu);
        if (cpu.fpul != cas[i][0] / cas[i][1]) {{ printf("%u / %u = %u\\n", cas[i][0], cas[i][1], cpu.fpul); return 1; }}
        if (cpu.fpscr != 0) {{ printf("FPSCR non remis : %08X\\n", cpu.fpscr); return 2; }}
    }}
    return 0;'''
        fini = executer(c, corps)
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)

    def test_simple_precision_inchangee(self):
        # FPSCR.PR = 0 : fadd fr1, fr2 reste une addition de deux FRn.
        compilateur = module.SH4Recompiler(b'\0\0', BASE)
        texte = compilateur.recompile_instruction(0xF210, BASE)[0]
        corps = f'''
    SH4CPU c; SH4CPU *cpu = &c; memset(&c, 0, sizeof c);
    c.fr[1] = 1.5f; c.fr[2] = 2.25f; c.fr[3] = 7.0f;
    {texte}
    return (c.fr[2] == 3.75f && c.fr[3] == 7.0f) ? 0 : 1;'''
        fini = executer(TEMPLATE, corps)
        self.assertEqual(fini.returncode, 0, fini.stdout + fini.stderr)


if __name__ == '__main__':
    unittest.main()
